"""Tests for the HuggingFace TTS provider adapter.

Fully offline: an injected ``httpx.MockTransport`` stands in for the HF Inference
API, a capturing in-memory ``sink`` stands in for storage, and the ``ffprobe``
exec seam is monkeypatched, so request building, the cold-start / non-audio
handling, the bytes → ``SynthesizedSpeech`` mapping, and the duration-probe
contract are all verified without a live call or the ``ffprobe`` binary. The live
call itself is covered by a separate ``@pytest.mark.integration`` smoke test
(skipped without an HF key). Mirrors ``tests/media/tts/test_http_tts.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import httpx
import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.huggingface import (
    DEFAULT_API_ROOT,
    HuggingFaceTtsError,
    HuggingFaceTtsProvider,
)


class _CapturingSink:
    """An in-memory ``AudioSink`` that records bytes and returns a stable URI."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.received.append(audio)
        return f"mem://audio/{len(self.received)}.flac"


def _provider(
    handler: Any,
    *,
    sink: Any | None = None,
    model: str = "espnet/kan-bayashi_ljspeech_vits",
) -> HuggingFaceTtsProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HuggingFaceTtsProvider(
        model=model,
        token="hf_secret",
        sink=sink or _CapturingSink(),
        client=client,
    )


def _audio_resp(audio: bytes) -> httpx.Response:
    return httpx.Response(200, content=audio, headers={"Content-Type": "audio/flac"})


def _synthesize(
    provider: HuggingFaceTtsProvider, *, text: str = "hello", voice: str = "narrator"
) -> SynthesizedSpeech:
    return asyncio.run(provider.synthesize(text=text, voice=voice))


@pytest.fixture(autouse=True)
def _stub_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the ffprobe exec seam with a fixed duration (no binary needed)."""
    monkeypatch.setattr(
        HuggingFaceTtsProvider,
        "_probe_duration_ms",
        lambda self, audio: 3210,
    )


def test_satisfies_protocol() -> None:
    assert isinstance(_provider(lambda r: _audio_resp(b"")), TTSProvider)


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}
    sink = _CapturingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return _audio_resp(b"fLaC-fake-audio")

    speech = _synthesize(
        _provider(handler, sink=sink, model="suno/bark"),
        text="hi there",
        voice="anchor",
    )

    # Request shape: model in the path, bare {"inputs": text} body (no voice),
    # bearer token from construction.
    assert seen["url"] == f"{DEFAULT_API_ROOT}/models/suno/bark"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer hf_secret"
    assert seen["body"] == {"inputs": "hi there"}

    # Response mapping into the descriptor.
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms == 3210  # from the stubbed ffprobe seam
    assert speech.voice == "anchor"  # echoed for provenance, never sent
    assert speech.produced_via == "tts:huggingface"
    assert speech.audio_uri == "mem://audio/1.flac"

    # Raw audio bytes were handed to the storage sink (not embedded in the DTO).
    assert sink.received == [b"fLaC-fake-audio"]


def test_trailing_slash_api_root_normalized() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _audio_resp(b"x")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HuggingFaceTtsProvider(
        model="m/v",
        token="hf_x",
        sink=_CapturingSink(),
        api_root="https://hf.example/api/",
        client=client,
    )
    _synthesize(provider)
    assert seen["url"] == "https://hf.example/api/models/m/v"


def test_cold_start_503_raises_with_estimate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "loading", "estimated_time": 41.7})

    with pytest.raises(HuggingFaceTtsError, match="estimated_time=42s"):
        _synthesize(_provider(handler))


def test_cold_start_503_without_estimate_still_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "loading"})

    with pytest.raises(HuggingFaceTtsError, match="cold-starting"):
        _synthesize(_provider(handler))


def test_other_http_error_propagates_as_httpx() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _synthesize(_provider(lambda r: httpx.Response(401, json={"error": "bad token"})))


def test_json_body_with_200_rejected_as_non_audio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "input too long"})

    with pytest.raises(HuggingFaceTtsError, match="JSON body where audio bytes"):
        _synthesize(_provider(handler))


def test_token_never_leaks_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "loading", "estimated_time": 5})

    with pytest.raises(HuggingFaceTtsError) as exc_info:
        _synthesize(_provider(handler))
    assert "hf_secret" not in str(exc_info.value)


def test_missing_model_raises() -> None:
    with pytest.raises(HuggingFaceTtsError, match="model is required"):
        HuggingFaceTtsProvider(model="", token="hf_x", sink=_CapturingSink())


def test_missing_token_raises() -> None:
    with pytest.raises(HuggingFaceTtsError, match="token is required"):
        HuggingFaceTtsProvider(model="m/v", token="", sink=_CapturingSink())


# --- ffprobe exec-seam contract (the autouse stub is overridden per-test) -----


def test_probe_parses_seconds_to_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drop the autouse stub; exercise the real _probe path

    class _Completed:
        returncode = 0
        stdout = b"2.5\n"
        stderr = b""

    monkeypatch.setattr(
        "app.media.tts.huggingface.subprocess.run",
        lambda *a, **k: _Completed(),
    )
    provider = _provider(lambda r: _audio_resp(b"audio"))
    assert provider._probe_duration_ms(b"audio") == 2500


def test_probe_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    def _raise(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("app.media.tts.huggingface.subprocess.run", _raise)
    provider = _provider(lambda r: _audio_resp(b"audio"))
    with pytest.raises(HuggingFaceTtsError, match="ffprobe binary not found"):
        provider._probe_duration_ms(b"audio")


def test_probe_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    class _Completed:
        returncode = 1
        stdout = b""
        stderr = b"pipe:0: Invalid data found"

    monkeypatch.setattr(
        "app.media.tts.huggingface.subprocess.run",
        lambda *a, **k: _Completed(),
    )
    provider = _provider(lambda r: _audio_resp(b"audio"))
    with pytest.raises(HuggingFaceTtsError, match="exited with code 1"):
        provider._probe_duration_ms(b"audio")


def test_probe_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    def _raise(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=60.0)

    monkeypatch.setattr("app.media.tts.huggingface.subprocess.run", _raise)
    provider = _provider(lambda r: _audio_resp(b"audio"))
    with pytest.raises(HuggingFaceTtsError, match="timed out"):
        provider._probe_duration_ms(b"audio")


def test_probe_unparseable_duration_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    class _Completed:
        returncode = 0
        stdout = b"N/A\n"
        stderr = b""

    monkeypatch.setattr(
        "app.media.tts.huggingface.subprocess.run",
        lambda *a, **k: _Completed(),
    )
    provider = _provider(lambda r: _audio_resp(b"audio"))
    with pytest.raises(HuggingFaceTtsError, match="unparseable duration"):
        provider._probe_duration_ms(b"audio")
