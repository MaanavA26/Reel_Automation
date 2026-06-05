"""Hermetic tests for the five generative-video adapters (documented contracts).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, and
``sleep`` is injected as a no-op so the poll loop runs instantly. Each adapter's
request construction (endpoint, auth, body) and its submit -> poll -> result
mapping are asserted against the *documented* wire shape (NOT live-validated; the
last-mile caveat — ADR 0053). Also covers the shared base loop: a failed job and a
poll-budget timeout both raise `GenerativeVisualError`; transport errors propagate
as ``httpx`` errors; keys never appear in reprs.

The poll-state progression is modeled by dispatching the MockTransport handler on
call count (first poll "pending", second "done"), mirroring how the YouTube
two-request test dispatches on method.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.media.visuals.base import VisualKind
from app.media.visuals.generative import GenerativeVisualError, GenerativeVisualProvider
from app.media.visuals.generative_providers.kling import (
    KlingGenerativeProvider,
    encode_jwt_hs256,
)
from app.media.visuals.generative_providers.luma import LumaGenerativeProvider
from app.media.visuals.generative_providers.pika import PikaGenerativeProvider
from app.media.visuals.generative_providers.runway import RunwayGenerativeProvider
from app.media.visuals.generative_providers.veo import VeoGenerativeProvider


async def _noop_sleep(_seconds: float) -> None:
    return None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "provider",
    [
        RunwayGenerativeProvider(api_key="k"),
        LumaGenerativeProvider(api_key="k"),
        PikaGenerativeProvider(api_key="k"),
        KlingGenerativeProvider(access_key="ak", secret_key="sk"),
        VeoGenerativeProvider(access_token="t", project="p", storage_uri="gs://b"),
    ],
)
def test_real_adapters_satisfy_protocol(provider: GenerativeVisualProvider) -> None:
    # Guards against a `generate` signature drift between the base/adapters and
    # the protocol (the only check that catches it when mypy can't run locally).
    assert isinstance(provider, GenerativeVisualProvider)


# --- Runway ----------------------------------------------------------------


def test_runway_submit_and_poll() -> None:
    seen: dict[str, Any] = {}
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/text_to_video":
            seen["auth"] = request.headers.get("Authorization")
            seen["version"] = request.headers.get("X-Runway-Version")
            seen["body"] = request.read()
            import json

            seen["json"] = json.loads(seen["body"])
            return httpx.Response(200, json={"id": "task-1"})
        # GET /v1/tasks/task-1
        assert request.url.path == "/v1/tasks/task-1"
        calls["poll"] += 1
        if calls["poll"] == 1:
            return httpx.Response(200, json={"status": "RUNNING"})
        return httpx.Response(200, json={"status": "SUCCEEDED", "output": ["https://cdn/r.mp4"]})

    provider = RunwayGenerativeProvider(api_key="k", client=_client(handler), sleep=_noop_sleep)
    clip = _run(provider.generate(prompt="ocean", duration_ms=5000, aspect="9:16"))

    assert seen["auth"] == "Bearer k"
    assert seen["version"] == "2024-11-06"
    assert seen["json"] == {
        "model": "gen4_turbo",
        "promptText": "ocean",
        "ratio": "720:1280",
        "duration": 5,
    }
    assert clip.uri == "https://cdn/r.mp4"
    assert clip.kind is VisualKind.VIDEO
    assert (clip.width, clip.height) == (1080, 1920)
    assert clip.produced_via == "genvideo:runway"
    assert calls["poll"] == 2


def test_runway_failed_job_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        return httpx.Response(200, json={"status": "FAILED", "failure": "nsfw"})

    provider = RunwayGenerativeProvider(api_key="k", client=_client(handler), sleep=_noop_sleep)
    with pytest.raises(GenerativeVisualError, match="failed"):
        _run(provider.generate(prompt="x"))


def test_runway_transport_error_propagates() -> None:
    provider = RunwayGenerativeProvider(
        api_key="k",
        client=_client(lambda r: httpx.Response(429, json={"e": "rate"})),
        sleep=_noop_sleep,
    )
    with pytest.raises(httpx.HTTPStatusError):
        _run(provider.generate(prompt="x"))


def test_runway_key_not_in_repr() -> None:
    provider = RunwayGenerativeProvider(api_key="super-secret")
    assert "super-secret" not in repr(provider)


# --- Luma ------------------------------------------------------------------


def test_luma_submit_and_poll() -> None:
    seen: dict[str, Any] = {}
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.method == "POST":
            assert request.url.path == "/dream-machine/v1/generations/video"
            seen["auth"] = request.headers.get("Authorization")
            seen["json"] = json.loads(request.read())
            return httpx.Response(201, json={"id": "gen-9", "state": "queued"})
        assert request.url.path == "/dream-machine/v1/generations/gen-9"
        calls["poll"] += 1
        if calls["poll"] == 1:
            return httpx.Response(200, json={"state": "dreaming"})
        return httpx.Response(
            200, json={"state": "completed", "assets": {"video": "https://cdn/l.mp4"}}
        )

    provider = LumaGenerativeProvider(api_key="k", client=_client(handler), sleep=_noop_sleep)
    clip = _run(provider.generate(prompt="forest", duration_ms=9000, aspect="9:16"))

    assert seen["auth"] == "Bearer k"
    assert seen["json"]["aspect_ratio"] == "9:16"
    assert seen["json"]["model"] == "ray-2"
    assert seen["json"]["duration"] == "9s"
    assert clip.uri == "https://cdn/l.mp4"
    assert clip.produced_via == "genvideo:luma"


def test_luma_failed_job_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "g"})
        return httpx.Response(200, json={"state": "failed", "failure_reason": "moderation"})

    provider = LumaGenerativeProvider(api_key="k", client=_client(handler), sleep=_noop_sleep)
    with pytest.raises(GenerativeVisualError, match="moderation"):
        _run(provider.generate(prompt="x"))


# --- Veo (Vertex AI LRO) ---------------------------------------------------


def test_veo_submit_and_poll() -> None:
    seen: dict[str, Any] = {}
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.read())
        if request.url.path.endswith(":predictLongRunning"):
            seen["submit_url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["submit_body"] = body
            return httpx.Response(200, json={"name": "operations/op-1"})
        assert request.url.path.endswith(":fetchPredictOperation")
        seen["poll_body"] = body
        calls["poll"] += 1
        if calls["poll"] == 1:
            return httpx.Response(200, json={"name": "operations/op-1", "done": False})
        return httpx.Response(
            200,
            json={
                "done": True,
                "response": {"videos": [{"gcsUri": "gs://bucket/out/v.mp4"}]},
            },
        )

    provider = VeoGenerativeProvider(
        access_token="tok",
        project="proj",
        location="us-central1",
        storage_uri="gs://bucket/out",
        client=_client(handler),
        sleep=_noop_sleep,
    )
    clip = _run(provider.generate(prompt="sunset", duration_ms=6000, aspect="9:16"))

    assert seen["auth"] == "Bearer tok"
    assert "projects/proj/locations/us-central1/publishers/google/models/" in seen["submit_url"]
    assert seen["submit_body"]["instances"] == [{"prompt": "sunset"}]
    assert seen["submit_body"]["parameters"]["storageUri"] == "gs://bucket/out"
    assert seen["submit_body"]["parameters"]["aspectRatio"] == "9:16"
    assert seen["poll_body"] == {"operationName": "operations/op-1"}
    assert clip.uri == "gs://bucket/out/v.mp4"
    assert clip.produced_via == "genvideo:veo"


def test_veo_operation_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "op"})
        return httpx.Response(200, json={"done": True, "error": {"message": "quota exceeded"}})

    provider = VeoGenerativeProvider(
        access_token="t",
        project="p",
        storage_uri="gs://b",
        client=_client(handler),
        sleep=_noop_sleep,
    )
    with pytest.raises(GenerativeVisualError, match="quota"):
        _run(provider.generate(prompt="x"))


def test_veo_requires_storage_uri() -> None:
    with pytest.raises(GenerativeVisualError, match="storage_uri"):
        VeoGenerativeProvider(access_token="t", project="p", storage_uri="")


# --- Kling (JWT auth) ------------------------------------------------------


def test_kling_jwt_is_well_formed_and_deterministic() -> None:
    token = encode_jwt_hs256(access_key="ak", secret_key="sk", now=1_000_000)
    parts = token.split(".")
    assert len(parts) == 3  # header.payload.signature
    # Same inputs -> same token (pure / deterministic with a fixed clock).
    assert token == encode_jwt_hs256(access_key="ak", secret_key="sk", now=1_000_000)

    import base64
    import json

    def _decode(seg: str) -> dict[str, Any]:
        padded = seg + "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))

    assert _decode(parts[0]) == {"alg": "HS256", "typ": "JWT"}
    payload = _decode(parts[1])
    assert payload["iss"] == "ak"
    assert payload["exp"] == 1_000_000 + 1800
    assert payload["nbf"] == 1_000_000 - 5


def test_kling_submit_and_poll() -> None:
    seen: dict[str, Any] = {}
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.method == "POST":
            assert request.url.path == "/v1/videos/text2video"
            seen["auth"] = request.headers.get("Authorization")
            seen["json"] = json.loads(request.read())
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "kt-1"}})
        assert request.url.path == "/v1/videos/text2video/kt-1"
        calls["poll"] += 1
        if calls["poll"] == 1:
            return httpx.Response(200, json={"data": {"task_status": "processing"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "task_status": "succeed",
                    "task_result": {"videos": [{"url": "https://cdn/k.mp4"}]},
                }
            },
        )

    provider = KlingGenerativeProvider(
        access_key="ak", secret_key="sk", client=_client(handler), sleep=_noop_sleep
    )
    clip = _run(provider.generate(prompt="city", duration_ms=5000, aspect="9:16"))

    assert seen["auth"].startswith("Bearer ")
    assert seen["json"] == {
        "model_name": "kling-v1",
        "prompt": "city",
        "aspect_ratio": "9:16",
        "duration": "5",
    }
    assert clip.uri == "https://cdn/k.mp4"
    assert clip.produced_via == "genvideo:kling"


def test_kling_failed_job_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"task_id": "t"}})
        return httpx.Response(
            200, json={"data": {"task_status": "failed", "task_status_msg": "bad prompt"}}
        )

    provider = KlingGenerativeProvider(
        access_key="ak", secret_key="sk", client=_client(handler), sleep=_noop_sleep
    )
    with pytest.raises(GenerativeVisualError, match="bad prompt"):
        _run(provider.generate(prompt="x"))


def test_kling_secret_not_in_repr() -> None:
    provider = KlingGenerativeProvider(access_key="ak", secret_key="super-secret")
    assert "super-secret" not in repr(provider)


# --- Pika (via fal queue) --------------------------------------------------


def test_pika_submit_status_and_fetch() -> None:
    seen: dict[str, Any] = {}
    calls = {"status": 0}
    status_url = "https://queue.fal.run/fal-ai/pika/v2.2/text-to-video/requests/r1/status"
    response_url = "https://queue.fal.run/fal-ai/pika/v2.2/text-to-video/requests/r1"

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        url = str(request.url)
        if request.method == "POST":
            seen["submit_path"] = request.url.path
            seen["auth"] = request.headers.get("Authorization")
            seen["json"] = json.loads(request.read())
            return httpx.Response(
                200,
                json={
                    "request_id": "r1",
                    "status_url": status_url,
                    "response_url": response_url,
                },
            )
        if url == status_url:
            calls["status"] += 1
            if calls["status"] == 1:
                return httpx.Response(200, json={"status": "IN_PROGRESS"})
            return httpx.Response(200, json={"status": "COMPLETED"})
        assert url == response_url
        return httpx.Response(200, json={"video": {"url": "https://cdn/p.mp4"}})

    provider = PikaGenerativeProvider(api_key="fal", client=_client(handler), sleep=_noop_sleep)
    clip = _run(provider.generate(prompt="rain", duration_ms=5000, aspect="9:16"))

    assert seen["submit_path"] == "/fal-ai/pika/v2.2/text-to-video"
    assert seen["auth"] == "Key fal"
    assert seen["json"] == {"prompt": "rain", "aspect_ratio": "9:16", "duration": 5}
    assert clip.uri == "https://cdn/p.mp4"
    assert clip.produced_via == "genvideo:pika"
    assert calls["status"] == 2


def test_pika_failed_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"status_url": "https://q/s", "response_url": "https://q/r"}
            )
        return httpx.Response(200, json={"status": "ERROR"})

    provider = PikaGenerativeProvider(api_key="fal", client=_client(handler), sleep=_noop_sleep)
    with pytest.raises(GenerativeVisualError, match="failed"):
        _run(provider.generate(prompt="x"))


# --- shared base loop ------------------------------------------------------


def test_poll_budget_timeout_raises() -> None:
    # Always-pending poll exhausts the (tiny) budget and raises, instantly,
    # because sleep is a no-op.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        return httpx.Response(200, json={"status": "RUNNING"})

    provider = RunwayGenerativeProvider(
        api_key="k", client=_client(handler), sleep=_noop_sleep, poll_attempts=3
    )
    with pytest.raises(GenerativeVisualError, match="did not finish"):
        _run(provider.generate(prompt="x"))


def test_done_without_result_uri_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        return httpx.Response(200, json={"status": "SUCCEEDED", "output": []})

    provider = RunwayGenerativeProvider(api_key="k", client=_client(handler), sleep=_noop_sleep)
    with pytest.raises(GenerativeVisualError, match="no result uri"):
        _run(provider.generate(prompt="x"))


def test_invalid_poll_attempts_rejected() -> None:
    with pytest.raises(GenerativeVisualError):
        RunwayGenerativeProvider(api_key="k", poll_attempts=0)
