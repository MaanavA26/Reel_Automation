"""Tests for the end-to-end video API surface (ADR 0032).

Hermetic and network-free: the router is driven through FastAPI's `TestClient`
with the `VideoPipeline` dependency overridden by a lightweight fake that returns
a canned `VideoArtifact` (the router only calls ``pipeline.create``). This keeps
the API test focused on the HTTP contract — request validation, status codes, the
async job lifecycle, and the 503 wiring-error mapping — while the full
research→media path is exercised by `tests/services/video/test_pipeline.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_video_pipeline
from app.main import create_app
from app.services.composition import CompositionError
from app.services.video import VideoArtifact, VideoPipelineError


def _artifact(topic: str) -> VideoArtifact:
    return VideoArtifact(
        topic=topic,
        research_state_id="job_x",
        creator_packet_id="pkt_x",
        media_plan_id="plan_x",
        narrative_title="Why it matters",
        video_uri="fake://composition/1.mp4",
        duration_ms=3000,
        width=1080,
        height=1920,
        produced_via="video:video",
    )


class _FakeVideoPipeline:
    """Duck-typed `VideoPipeline` for the router (only ``create`` is called)."""

    async def create(self, topic: str, *, narrative_index: int = 0) -> VideoArtifact:
        return _artifact(topic)


class _FailingVideoPipeline:
    async def create(self, topic: str, *, narrative_index: int = 0) -> VideoArtifact:
        raise VideoPipelineError("research run did not complete")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient whose video-pipeline dependency is overridden with a fake."""
    app = create_app()
    app.dependency_overrides[get_video_pipeline] = lambda: _FakeVideoPipeline()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Synchronous POST /videos ------------------------------------------------


def test_create_video_returns_artifact(client: TestClient) -> None:
    response = client.post("/api/v1/videos", json={"topic": "fusion energy"})

    assert response.status_code == 200
    body = response.json()
    assert body["topic"] == "fusion energy"
    assert body["narrative_title"] == "Why it matters"
    assert body["video_uri"] == "fake://composition/1.mp4"
    assert body["id"].startswith("reel_")
    assert body["produced_via"] == "video:video"


def test_create_video_rejects_empty_topic(client: TestClient) -> None:
    assert client.post("/api/v1/videos", json={"topic": ""}).status_code == 422


def test_create_video_rejects_missing_topic(client: TestClient) -> None:
    assert client.post("/api/v1/videos", json={}).status_code == 422


def test_create_video_rejects_negative_narrative_index(client: TestClient) -> None:
    response = client.post("/api/v1/videos", json={"topic": "t", "narrative_index": -1})
    assert response.status_code == 422


# --- Wiring failures map to 503 (inherited from the app-wide handler) --------


def test_composition_error_maps_to_503() -> None:
    def _raise() -> object:
        raise CompositionError("media render requires REEL_AUTOMATION_TTS_BASE_URL")

    app = create_app()
    app.dependency_overrides[get_video_pipeline] = _raise
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/api/v1/videos", json={"topic": "t"})

    assert response.status_code == 503
    assert "TTS_BASE_URL" in response.json()["detail"]


# --- Async job surface (POST /videos/jobs + GET /videos/jobs/{id}) -----------
# Under TestClient, BackgroundTasks run to completion before the POST returns, so
# the POST body is the QUEUED snapshot and the first GET reflects the terminal one.


def test_enqueue_video_job_returns_queued_id(client: TestClient) -> None:
    response = client.post("/api/v1/videos/jobs", json={"topic": "fusion"})

    assert response.status_code == 202
    body = response.json()
    assert body["topic"] == "fusion"
    assert body["id"].startswith("vjob_")
    assert body["status"] == "queued"
    assert body["artifact"] is None


def test_enqueue_then_get_reaches_completed_with_artifact(client: TestClient) -> None:
    job_id = client.post("/api/v1/videos/jobs", json={"topic": "t"}).json()["id"]

    result = client.get(f"/api/v1/videos/jobs/{job_id}")

    assert result.status_code == 200
    body = result.json()
    assert body["id"] == job_id
    assert body["status"] == "completed"
    assert body["artifact"]["video_uri"] == "fake://composition/1.mp4"


def test_failed_job_records_failed_status() -> None:
    app = create_app()
    app.dependency_overrides[get_video_pipeline] = lambda: _FailingVideoPipeline()
    with TestClient(app) as test_client:
        job_id = test_client.post("/api/v1/videos/jobs", json={"topic": "t"}).json()["id"]
        body = test_client.get(f"/api/v1/videos/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert "did not complete" in body["error"]
    assert body["artifact"] is None
    app.dependency_overrides.clear()


def test_get_unknown_video_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/videos/jobs/vjob_nope")

    assert response.status_code == 404
    assert "vjob_nope" in response.json()["detail"]
