"""Benchmark: end-to-end ``run_research`` latency with fake providers (offline).

Drives the whole Deep Research pipeline (``plan -> acquire -> ingest -> extract
-> verify -> synthesize -> critique -> report -> packet -> publish``) through the
*real* compiled LangGraph graph, backed end-to-end by hermetic ``FakeProvider``
agents + fetch/search fakes — no network, no credentials, fully deterministic.
This measures the orchestration overhead (graph compile + ainvoke + per-node
state copy/merge + strict re-validation) with model latency factored out, which
is the part of the latency the engine itself owns.

Marked ``@pytest.mark.integration`` so it is **deselected from the default
suite** (via the project's ``addopts = "-m 'not integration'"``) — *not* because
it needs network (it is hermetic), but because ``pyproject.toml`` (where a
dedicated default-deselect marker would live) is out of scope here and
``integration`` is the existing "do not run by default" lever. It also carries
``@pytest.mark.perf`` (registered in this package's ``conftest``) as the positive
selector, so ``pytest -m perf`` runs only the benchmarks without dragging in the
network-gated live ``integration`` tests. Informational timing, not a gate: the
only assertion is that the run reached ``COMPLETED``, never a latency threshold.

The fake-backed deps are built **inline** here (mirroring ``test_pipeline_e2e``):
``FakeProvider`` replays a fixed script and is consumed per run, so each timed
iteration constructs a fresh ``ResearchDeps`` bundle. There is no top-level
``tests/__init__.py``, so importing the workflow-test fixtures by dotted path
would not resolve without touching another test area (out of scope) — hence the
small self-contained mirror.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

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
from app.schemas.research_state import (
    JobStatus,
    ResearchState,
    SourceType,
    SupportLevel,
)
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider
from app.workflows.deep_research import ResearchDeps, run_research
from tests.perf.harness import TimingResult, render_table, time_callable

# --- Self-contained fake-backed deps (mirror tests/integration/test_pipeline_e2e) ---


def _router(output: object, role: ModelRole = ModelRole.PLANNING) -> ModelRouter:
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={role: ModelChoice("fake", "fake-model")},
    )


def _planner() -> ResearchPlannerAgent:
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in ("q1", "q2")],
    )
    return ResearchPlannerAgent(_router(output))


def _discovery(n_sources: int) -> SourceDiscoveryAgent:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
            for i in range(n_sources)
        ]
    )
    return SourceDiscoveryAgent(_router(output), search)


def _ingestion(n_sources: int) -> IngestionService:
    by_url = {
        f"https://s{i}.com": FetchedContent(
            url=f"https://s{i}.com", content=f"<p>body {i}</p>".encode(), content_type="text/html"
        )
        for i in range(n_sources)
    }
    return IngestionService(FakeFetchProvider(by_url))


def _extractor(n_chunks: int) -> EvidenceExtractionAgent:
    outputs = [
        _ExtractionOutput(claims=[_ExtractedClaim(claim=f"claim {i}", confidence=0.8)])
        for i in range(max(n_chunks, 1))
    ]
    return EvidenceExtractionAgent(
        ModelRouter(
            providers={"fake": FakeProvider(outputs)},
            policy={ModelRole.EXTRACTION: ModelChoice("fake", "fake-model")},
        )
    )


def _verifier() -> CrossVerificationAgent:
    output = _VerificationOutput(
        verdicts=[
            _VerdictDraft(
                claim="verdict",
                support_level=SupportLevel.SINGLE_SOURCE,
                confidence=0.7,
                supporting=[0],
            )
        ]
    )
    return CrossVerificationAgent(_router(output))


def _synthesizer() -> SynthesisAgent:
    output = _SynthesisOutput(
        findings=[
            _FindingDraft(
                statement="finding", supporting_verdicts=[0], sub_questions=[0, 1, 2, 3, 4]
            )
        ]
    )
    return SynthesisAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


def _critic() -> EditorialCriticAgent:
    return EditorialCriticAgent(_router(_CritiqueOutput(issues=[], rationale="ok")))


def _reporter() -> ReportAgent:
    output = _ReportOutput(
        title="t", abstract="a", sections=[_SectionDraft(heading="h", narrative="n", findings=[0])]
    )
    return ReportAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


def _strategist() -> CreatorPacketAgent:
    output = _PacketOutput(hooks=[_HookDraft(text="hook", findings=[0])])
    return CreatorPacketAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


def _make_deps(n_sources: int) -> ResearchDeps:
    """Assemble a fresh fake-backed dependency bundle (single-shot fakes)."""
    return ResearchDeps(
        planner=_planner(),
        discovery=_discovery(n_sources),
        ingestion=_ingestion(n_sources),
        extractor=_extractor(n_sources),
        verifier=_verifier(),
        synthesizer=_synthesizer(),
        critic=_critic(),
        reporter=_reporter(),
        strategist=_strategist(),
    )


def _run_once(n_sources: int) -> ResearchState:
    """One full hermetic pipeline run with a freshly-built dep bundle."""
    deps = _make_deps(n_sources)
    return asyncio.run(run_research(ResearchState(topic="benchmark topic"), deps=deps))


@pytest.mark.integration
@pytest.mark.perf
def test_run_research_end_to_end_latency(record_perf_table: Callable[[str], None]) -> None:
    """Time the full hermetic ``run_research`` pipeline; print a timing table.

    Two source counts are timed as a sanity-vary, not a scaling demonstration:
    the measured region is dominated by the fixed orchestration cost (graph
    compile + ``ainvoke`` + per-node state copy/merge + strict re-validation), so
    the per-source work (more chunks/evidence/extraction calls) is negligible at
    this scale and the two rows are expected to land close together — confirming
    end-to-end latency is corpus-insensitive here (the input-size curve is the
    ``build_claim_blocks`` benchmark's job). Each timed call rebuilds the
    single-shot fakes, so the construction cost is folded into the measurement
    (it is part of a real run's setup).
    """
    results: list[TimingResult] = []
    for n_sources in (2, 8):
        result, final = time_callable(
            f"run_research(sources={n_sources})",
            lambda n=n_sources: _run_once(n),
            repeats=5,
        )
        assert final.status is JobStatus.COMPLETED, "benchmark run did not complete"
        results.append(result)

    record_perf_table(render_table("run_research end-to-end latency", results))
