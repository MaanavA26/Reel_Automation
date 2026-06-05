"""Tests for the Deep Research LangGraph workflow.

These tests assert the *state-threading contract* every node depends on:
lifecycle transitions, job-identity stability, that list-channel writes survive
the merge, that the final state re-validates under the strict schema, and that
the real ``plan`` (M3) / ``acquire`` (M5) / ``ingest`` (M6) / ``extract`` (M7) /
``verify`` (M8) / ``synthesize`` (M9) / ``critique`` (M10a) nodes populate state
end-to-end. They run the real compiled graph with `FakeProvider`-backed agents +
fakes (hermetic — no network) and drive the async entrypoint with ``asyncio.run``
(no ``pytest-asyncio`` dependency required). Dependencies are injected as a single
`ResearchDeps` bundle (ADR 0009).
"""

from __future__ import annotations

import asyncio

from app.agents.cross_verification import (
    CrossVerificationAgent,
    _VerdictDraft,
    _VerificationOutput,
)
from app.agents.editorial_critic import (
    EditorialCriticAgent,
    _CritiqueOutput,
)
from app.agents.evidence_extraction import (
    EvidenceExtractionAgent,
    _ExtractedClaim,
    _ExtractionOutput,
)
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
from app.agents.synthesis import (
    SynthesisAgent,
    _FindingDraft,
    _SynthesisOutput,
)
from app.schemas.research_state import JobStatus, ResearchState, SourceType, SupportLevel
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider
from app.workflows.deep_research import (
    ResearchDeps,
    _make_plan_node,
    build_research_graph,
    publish_node,
    run_research,
)


def _router(output: _PlannerOutput | _DiscoveryOutput) -> ModelRouter:
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )


def _planner(sub_question_texts: tuple[str, ...] = ("q1", "q2")) -> ResearchPlannerAgent:
    """A planner backed by a fake provider returning the given sub-questions."""
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in sub_question_texts],
    )
    return ResearchPlannerAgent(_router(output))


def _empty_planner() -> ResearchPlannerAgent:
    """A planner whose model returns no sub-questions, so plan() raises PlannerError."""
    return ResearchPlannerAgent(_router(_PlannerOutput(sub_questions=[])))


def _discovery(n_sources: int = 2) -> SourceDiscoveryAgent:
    """A discovery agent: model emits one query, fake search returns n sources."""
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
            for i in range(n_sources)
        ]
    )
    return SourceDiscoveryAgent(_router(output), search)


def _ingestion(n_sources: int = 2) -> IngestionService:
    """Ingestion service whose fake fetcher serves the discovery agent's URLs."""
    by_url = {
        f"https://s{i}.com": FetchedContent(
            url=f"https://s{i}.com", content=f"<p>body {i}</p>".encode(), content_type="text/html"
        )
        for i in range(n_sources)
    }
    return IngestionService(FakeFetchProvider(by_url))


def _extractor(n_chunks: int = 2) -> EvidenceExtractionAgent:
    """Extraction agent: scripts one claim per expected chunk (1 chunk per small source)."""
    outputs = [
        _ExtractionOutput(claims=[_ExtractedClaim(claim=f"claim {i}", confidence=0.8)])
        for i in range(max(n_chunks, 1))
    ]
    router = ModelRouter(
        providers={"fake": FakeProvider(outputs)},
        policy={ModelRole.EXTRACTION: ModelChoice("fake", "extract-model")},
    )
    return EvidenceExtractionAgent(router)


def _verifier() -> CrossVerificationAgent:
    """Verification agent: the fake-extracted claims all share the token "claim",
    so the blocker groups them into one cluster → one scripted model call."""
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
    router = ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return CrossVerificationAgent(router)


def _synthesizer() -> SynthesisAgent:
    """Synthesis agent: scripts one finding citing the first verdict + sub-question."""
    output = _SynthesisOutput(
        findings=[_FindingDraft(statement="finding", supporting_verdicts=[0], sub_questions=[0])]
    )
    router = ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "long-context-model")},
    )
    return SynthesisAgent(router)


def _critic() -> EditorialCriticAgent:
    """Editorial critic: scripts an empty issue list (decision is code-derived)."""
    router = ModelRouter(
        providers={"fake": FakeProvider([_CritiqueOutput(issues=[], rationale="ok")])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return EditorialCriticAgent(router)


def _deps(n_sources: int = 2, planner: ResearchPlannerAgent | None = None) -> ResearchDeps:
    return ResearchDeps(
        planner=planner or _planner(),
        discovery=_discovery(n_sources),
        ingestion=_ingestion(n_sources),
        extractor=_extractor(n_sources),
        verifier=_verifier(),
        synthesizer=_synthesizer(),
        critic=_critic(),
    )


def _run(topic: str = "t") -> ResearchState:
    return asyncio.run(run_research(ResearchState(topic=topic), deps=_deps()))


def test_graph_compiles() -> None:
    assert build_research_graph(_deps()) is not None


def test_run_research_reaches_completed() -> None:
    assert _run("quantum computing").status is JobStatus.COMPLETED


def test_run_research_returns_typed_state() -> None:
    # ainvoke returns a dict; the entrypoint must hand back a ResearchState.
    assert isinstance(_run(), ResearchState)


def test_job_identity_stable_across_run() -> None:
    # Partial-dict returns never reconstruct state, so id/created_at are
    # preserved by construction (ADR 0002).
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial, deps=_deps()))
    assert final.id == initial.id
    assert final.created_at == initial.created_at


def test_updated_at_advances() -> None:
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial, deps=_deps()))
    assert final.updated_at >= initial.updated_at


def test_plan_node_populates_plan() -> None:
    # M3: the plan node is bound to the planner and writes state.plan.
    final = asyncio.run(
        run_research(ResearchState(topic="t"), deps=_deps(planner=_planner(("a", "b", "c"))))
    )
    assert [sq.text for sq in final.plan.sub_questions] == ["a", "b", "c"]


def test_acquire_node_populates_sources() -> None:
    # M5: the acquire node is bound to the discovery agent; the sources it
    # produces survive the single channel write (reducer-deferral guard, ADR 0006).
    final = asyncio.run(run_research(ResearchState(topic="t"), deps=_deps(n_sources=3)))
    assert len(final.acquisition.sources) == 3
    assert all(s.discovered_via == "search:fake" for s in final.acquisition.sources)


def test_ingest_node_populates_chunks() -> None:
    # M6: the ingest node fetches+parses+chunks the discovered sources.
    final = _run()
    assert final.acquisition.chunks, "ingest node produced no chunks"
    assert all(c.source_id for c in final.acquisition.chunks)


def test_extract_node_populates_evidence() -> None:
    # M7: the extract node turns chunks into grounded Evidence.
    final = _run()
    assert final.acquisition.evidence, "extract node produced no evidence"
    ev = final.acquisition.evidence[0]
    assert ev.chunk_id and ev.source_id and ev.source_url
    assert ev.extracted_via.startswith("extraction:")


def test_verify_node_populates_verdicts() -> None:
    # M8: the verify node cross-checks evidence into reasoning verdicts.
    final = _run()
    assert final.reasoning.verdicts, "verify node produced no verdicts"
    vd = final.reasoning.verdicts[0]
    assert vd.supporting_evidence_ids
    assert vd.verified_via.startswith("verification:")


def test_synthesize_node_populates_findings() -> None:
    # M9: the synthesize node composes verdicts into reasoning.synthesis findings.
    final = _run()
    assert final.reasoning.synthesis.findings, "synthesize node produced no findings"
    f = final.reasoning.synthesis.findings[0]
    assert f.supporting_verdict_ids
    assert f.synthesized_via.startswith("synthesis:")


def test_critique_node_populates_critique() -> None:
    # M10a: the critique node assesses the synthesis into a reasoning critique.
    final = _run()
    assert final.reasoning.critiques, "critique node produced no critique"
    c = final.reasoning.critiques[0]
    assert c.critiqued_via.startswith("critique:")
    assert c.decision is not None


def test_mixed_source_types_only_web_ingested() -> None:
    # M6: discovery yields a WEB and a PDF source; v1 ingests only WEB, skips the
    # PDF, and the job still COMPLETES (no crash on the unsupported type).
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url="https://web.com", source_type=SourceType.WEB),
            SearchResult(url="https://doc.com/f.pdf", source_type=SourceType.PDF),
        ]
    )
    deps = ResearchDeps(
        planner=_planner(),
        discovery=SourceDiscoveryAgent(_router(output), search),
        ingestion=IngestionService(
            FakeFetchProvider(
                {
                    "https://web.com": FetchedContent(
                        url="https://web.com", content=b"<p>web body</p>", content_type="text/html"
                    )
                }
            )
        ),
        extractor=_extractor(1),
        verifier=_verifier(),
        synthesizer=_synthesizer(),
        critic=_critic(),
    )
    final = asyncio.run(run_research(ResearchState(topic="t"), deps=deps))
    assert final.status is JobStatus.COMPLETED
    assert final.acquisition.chunks
    web_ids = {s.id for s in final.acquisition.sources if s.type is SourceType.WEB}
    assert all(c.source_id in web_ids for c in final.acquisition.chunks)


def test_plan_node_transitions_to_running() -> None:
    # The QUEUED -> RUNNING transition is part of the node contract; assert it
    # at the node seam (publish later overwrites status to COMPLETED).
    node = _make_plan_node(_planner())
    update = asyncio.run(node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.RUNNING


def test_final_state_revalidates_strict() -> None:
    assert ResearchState.model_validate(_run().model_dump())


def test_real_substates_present() -> None:
    final = _run()
    assert final.plan is not None
    assert final.acquisition is not None
    assert final.reasoning is not None


def test_publish_node_transitions_to_completed() -> None:
    update = asyncio.run(publish_node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.COMPLETED


# --- M4: error handling + conditional routing (ADR 0005) --------------------


def test_planner_failure_routes_to_failed() -> None:
    # A raised PlannerError is converted to a FAILED state update and
    # short-circuits the pipeline; run_research returns rather than crashing.
    final = asyncio.run(
        run_research(ResearchState(topic="t"), deps=_deps(planner=_empty_planner()))
    )
    assert final.status is JobStatus.FAILED
    assert final.error is not None
    assert "PlannerError" in final.error


def test_failed_run_short_circuits_remaining_bands() -> None:
    # Failure at plan routes to the terminal sink, so acquire never runs.
    final = asyncio.run(
        run_research(ResearchState(topic="t"), deps=_deps(planner=_empty_planner()))
    )
    assert final.acquisition.sources == []


def test_happy_path_leaves_error_unset() -> None:
    final = _run()
    assert final.status is JobStatus.COMPLETED
    assert final.error is None


def test_discovery_failure_routes_to_failed() -> None:
    # A real acquire-band exception (DiscoveryError, from empty search) is
    # converted to FAILED and short-circuits (ADR 0005 contract).
    final = asyncio.run(run_research(ResearchState(topic="t"), deps=_deps(n_sources=0)))
    assert final.status is JobStatus.FAILED
    assert final.error is not None
    assert "DiscoveryError" in final.error
