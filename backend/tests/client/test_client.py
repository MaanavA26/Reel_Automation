"""Hermetic tests for `ReelAutomationClient`.

Network-free: the client is driven through an `httpx.MockTransport` whose
handler records the request and returns a canned response, so each test asserts
the client (a) hits the right method + path and serializes the right body, and
(b) parses the response back into the canonical typed models. Response fixtures
are built **from the models themselves** (`ResearchState(...).model_dump(...)`)
so they cannot drift from the real schema.
"""

from __future__ import annotations

import httpx
import pytest

from app.client import ReelAutomationAPIError, ReelAutomationClient
from app.client.client import API_V1_PREFIX
from app.schemas.health import HealthResponse
from app.schemas.research_state import JobStatus, ResearchState

# --- fixtures built from the real models (so they stay in sync) -------------


def _state_json(topic: str = "octopus cognition", status: JobStatus = JobStatus.COMPLETED) -> dict:
    state = ResearchState(topic=topic, status=status)
    return state.model_dump(mode="json")


def _health_json() -> dict:
    return HealthResponse(status="ok", service="reel-automation").model_dump(mode="json")


def _client_with(handler) -> ReelAutomationClient:
    """Build a client wired to a `MockTransport` running ``handler``."""
    return ReelAutomationClient(
        "http://test.local",
        transport=httpx.MockTransport(handler),
    )


# --- health -----------------------------------------------------------------


def test_health_hits_path_and_parses() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json=_health_json())

    with _client_with(handler) as client:
        result = client.health()

    assert seen == {"method": "GET", "path": f"{API_V1_PREFIX}/health"}
    assert isinstance(result, HealthResponse)
    assert result.status == "ok"
    assert result.service == "reel-automation"


# --- submit_research (synchronous) ------------------------------------------


def test_submit_research_posts_topic_and_parses_state() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_state_json(topic="octopus cognition"))

    with _client_with(handler) as client:
        result = client.submit_research("octopus cognition")

    assert captured["method"] == "POST"
    assert captured["path"] == f"{API_V1_PREFIX}/research"
    # max_syntheses omitted when not supplied -> server default applies.
    assert captured["body"] == {"topic": "octopus cognition"}
    assert isinstance(result, ResearchState)
    assert result.topic == "octopus cognition"
    assert result.status == JobStatus.COMPLETED


def test_submit_research_includes_max_syntheses_when_set() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_state_json())

    with _client_with(handler) as client:
        client.submit_research("topic", max_syntheses=3)

    assert captured["body"] == {"topic": "topic", "max_syntheses": 3}


# --- enqueue_job (async) ----------------------------------------------------


def test_enqueue_job_returns_id() -> None:
    seen: dict[str, str] = {}
    queued = ResearchState(topic="topic", status=JobStatus.QUEUED)

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(202, json=queued.model_dump(mode="json"))

    with _client_with(handler) as client:
        job_id = client.enqueue_job("topic")

    assert seen == {"method": "POST", "path": f"{API_V1_PREFIX}/research/jobs"}
    assert job_id == queued.id


# --- get_job ----------------------------------------------------------------


def test_get_job_hits_id_path_and_parses() -> None:
    seen: dict[str, str] = {}
    job_id = "job_abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_state_json(status=JobStatus.RUNNING))

    with _client_with(handler) as client:
        result = client.get_job(job_id)

    assert seen["path"] == f"{API_V1_PREFIX}/research/jobs/{job_id}"
    assert isinstance(result, ResearchState)
    assert result.status == JobStatus.RUNNING


def test_get_job_unknown_id_raises_typed_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no research job with id 'nope'"})

    with _client_with(handler) as client, pytest.raises(ReelAutomationAPIError) as excinfo:
        client.get_job("nope")

    assert excinfo.value.status_code == 404
    assert "no research job" in excinfo.value.detail


# --- error handling ---------------------------------------------------------


def test_non_2xx_raises_typed_error_with_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "no production adapter wired"})

    with _client_with(handler) as client, pytest.raises(ReelAutomationAPIError) as excinfo:
        client.submit_research("topic")

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "no production adapter wired"


def test_non_json_error_body_falls_back_to_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with _client_with(handler) as client, pytest.raises(ReelAutomationAPIError) as excinfo:
        client.health()

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Internal Server Error"
