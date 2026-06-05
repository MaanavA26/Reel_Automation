"""Tests for the Deep Research API surface.

Hermetic and network-free: the router is driven through FastAPI's `TestClient`
with a fake-backed `ResearchDeps` injected via `app.dependency_overrides`, so the
real workflow runs end-to-end against `FakeProvider`/`FakeSearchProvider`/
`FakeFetchProvider` with no live calls. The fake-deps assembly mirrors the
workflow test's `_deps()` helper (it exercises the same agents) — kept local
here so the API test owns its own fixtures and stays decoupled from that module.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agents.creator_packet import CreatorPacketAgent, _HookDraft, _PacketOutput
from app.agents.cross_verification import (
    CrossVerificationAgent,
    _VerdictDraft,
    _VerificationOutput,
)
from app.agents.editorial_critic import EditorialCriticAgent, _CritiqueOutput
from app.agents.evidence_extraction import (
    EvidenceExtractionAgent,
    _ExtractedClaim,
    _ExtractionOutput,
)
from app.agents.report import ReportAgent, _ReportOutput, _SectionDraft
from app.agents.research_planner import (
    ResearchPlannerAgent,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.agents.source_discovery import (
    SourceDiscoveryAgent,
    _DiscoveryOutput,
    _DiscoveryQuery,
)
from app.agents.synthesis import SynthesisAgent, _FindingDraft, _SynthesisOutput
from app.api.deps import get_research_deps
from app.main import create_app
from app.schemas.research_state import JobStatus, SourceType, SupportLevel
from app.services.composition import CompositionError
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider
from app.workflows.deep_research import ResearchDeps

_N_SOURCES = 2


def _router(output: object) -> ModelRouter:
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={
            ModelRole.PLANNING: ModelChoice("fake", "m"),
            ModelRole.EXTRACTION: ModelChoice("fake", "m"),
            ModelRole.LONG_CONTEXT: ModelChoice("fake", "m"),
        },
    )


def _fake_deps() -> ResearchDeps:
    """A fully fake-backed deps bundle that drives the happy path to COMPLETED."""
    planner = ResearchPlannerAgent(
        _router(_PlannerOutput(goal="g", sub_questions=[_PlannerSubQuestion(text="q1")]))
    )
    discovery = SourceDiscoveryAgent(
        _router(_DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])),
        FakeSearchProvider(
            [
                SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
                for i in range(_N_SOURCES)
            ]
        ),
    )
    ingestion = IngestionService(
        FakeFetchProvider(
            {
                f"https://s{i}.com": FetchedContent(
                    url=f"https://s{i}.com",
                    content=f"<p>body {i}</p>".encode(),
                    content_type="text/html",
                )
                for i in range(_N_SOURCES)
            }
        )
    )
    extractor = EvidenceExtractionAgent(
        ModelRouter(
            providers={
                "fake": FakeProvider(
                    [
                        _ExtractionOutput(
                            claims=[_ExtractedClaim(claim=f"claim {i}", confidence=0.8)]
                        )
                        for i in range(_N_SOURCES)
                    ]
                )
            },
            policy={ModelRole.EXTRACTION: ModelChoice("fake", "m")},
        )
    )
    verifier = CrossVerificationAgent(
        _router(
            _VerificationOutput(
                verdicts=[
                    _VerdictDraft(
                        claim="verdict",
                        support_level=SupportLevel.SINGLE_SOURCE,
                        confidence=0.7,
                        supporting=[0],
                    )
                ]
            )
        )
    )
    synthesizer = SynthesisAgent(
        _router(
            _SynthesisOutput(
                findings=[
                    _FindingDraft(
                        statement="finding", supporting_verdicts=[0], sub_questions=[0, 1, 2, 3, 4]
                    )
                ]
            )
        )
    )
    critic = EditorialCriticAgent(_router(_CritiqueOutput(issues=[], rationale="ok")))
    reporter = ReportAgent(
        _router(
            _ReportOutput(
                title="t",
                abstract="a",
                sections=[_SectionDraft(heading="h", narrative="n", findings=[0])],
            )
        )
    )
    strategist = CreatorPacketAgent(
        _router(_PacketOutput(hooks=[_HookDraft(text="hook", findings=[0])]))
    )
    return ResearchDeps(
        planner=planner,
        discovery=discovery,
        ingestion=ingestion,
        extractor=extractor,
        verifier=verifier,
        synthesizer=synthesizer,
        critic=critic,
        reporter=reporter,
        strategist=strategist,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient whose research-deps dependency is overridden with fakes."""
    app = create_app()
    app.dependency_overrides[get_research_deps] = _fake_deps
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_submit_research_job_returns_completed_state(client: TestClient) -> None:
    response = client.post("/api/v1/research", json={"topic": "quantum computing"})

    assert response.status_code == 200
    body = response.json()
    assert body["topic"] == "quantum computing"
    assert body["status"] == JobStatus.COMPLETED.value


def test_submit_research_job_response_is_valid_research_state(client: TestClient) -> None:
    # The response must round-trip the full typed ResearchState contract.
    response = client.post("/api/v1/research", json={"topic": "t"})

    body = response.json()
    assert "id" in body and body["id"].startswith("job_")
    assert "plan" in body and body["plan"]["sub_questions"][0]["text"] == "q1"
    assert "acquisition" in body and "reasoning" in body


def test_submit_research_job_accepts_max_syntheses(client: TestClient) -> None:
    response = client.post("/api/v1/research", json={"topic": "t", "max_syntheses": 3})

    assert response.status_code == 200


def test_submit_research_job_rejects_empty_topic(client: TestClient) -> None:
    response = client.post("/api/v1/research", json={"topic": ""})

    assert response.status_code == 422


def test_submit_research_job_rejects_missing_topic(client: TestClient) -> None:
    response = client.post("/api/v1/research", json={})

    assert response.status_code == 422


def test_submit_research_job_rejects_out_of_range_max_syntheses(client: TestClient) -> None:
    response = client.post("/api/v1/research", json={"topic": "t", "max_syntheses": 99})

    assert response.status_code == 422


def test_composition_error_maps_to_503() -> None:
    # Without an override, the real composition root runs and fails loud (no
    # production search/model adapter is wired yet) -> surfaced as 503, not 500.
    def _raise() -> ResearchDeps:
        raise CompositionError("no production SearchProvider is wired yet")

    app = create_app()
    app.dependency_overrides[get_research_deps] = _raise
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/api/v1/research", json={"topic": "t"})

    assert response.status_code == 503
    assert "SearchProvider" in response.json()["detail"]


def test_default_composition_root_fails_loud() -> None:
    # The real (un-overridden) wiring must raise CompositionError rather than
    # silently returning a fake-backed or half-built bundle.
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/api/v1/research", json={"topic": "t"})

    assert response.status_code == 503


# --- Async job surface (POST /research/jobs + GET /research/jobs/{id}) -------
# Under TestClient, BackgroundTasks run to completion *before* the POST response
# returns, so: the POST body is the QUEUED snapshot (serialized pre-run), and the
# first GET already reflects the terminal state — no polling/sleep needed.


def test_enqueue_research_job_returns_queued_id(client: TestClient) -> None:
    response = client.post("/api/v1/research/jobs", json={"topic": "quantum computing"})

    assert response.status_code == 202
    body = response.json()
    assert body["topic"] == "quantum computing"
    assert body["id"].startswith("job_")
    # The enqueue response is the pre-run snapshot.
    assert body["status"] == JobStatus.QUEUED.value


def test_enqueue_then_get_reaches_completed(client: TestClient) -> None:
    enqueue = client.post("/api/v1/research/jobs", json={"topic": "t"})
    job_id = enqueue.json()["id"]

    # The background task has already run by the time the POST returned.
    result = client.get(f"/api/v1/research/jobs/{job_id}")

    assert result.status_code == 200
    body = result.json()
    assert body["id"] == job_id
    assert body["status"] == JobStatus.COMPLETED.value
    # The terminal snapshot carries the full result, not just a status.
    assert body["plan"]["sub_questions"][0]["text"] == "q1"


def test_enqueue_job_accepts_max_syntheses(client: TestClient) -> None:
    response = client.post("/api/v1/research/jobs", json={"topic": "t", "max_syntheses": 3})

    assert response.status_code == 202


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/research/jobs/job_does_not_exist")

    assert response.status_code == 404
    assert "job_does_not_exist" in response.json()["detail"]


def test_enqueue_job_rejects_empty_topic(client: TestClient) -> None:
    response = client.post("/api/v1/research/jobs", json={"topic": ""})

    assert response.status_code == 422
