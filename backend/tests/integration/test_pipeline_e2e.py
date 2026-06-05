"""Hermetic full-pipeline end-to-end tests for the Deep Research workflow.

This file lives under ``tests/integration/`` but is deliberately **not** marked
``@pytest.mark.integration``: it is fully hermetic (``FakeProvider``-backed agents
+ fetch/search fakes, no network, no credentials) and so must run in the default
``pytest`` suite. It earns its place in ``integration/`` because it spans *every*
band of the workflow at once — the cross-band integration that no single
milestone (per-band) test in ``tests/workflows/`` covers.

Where the milestone tests assert each band populates its own slice of state, the
tests here drive ``run_research`` end-to-end and assert the bands are mutually
*coherent*: the provenance graph
``Finding -> Verdict -> Evidence -> Chunk -> Source`` resolves with no dangling
ids, and the published report's citations and caveats reference only entities
that exist in the reasoning/acquisition state. The invariant direction is always
**referenced-id -> exists** (never "every entity is referenced"): clustering and
coverage are legitimately lossy, so the integrity claim is that nothing points at
a phantom, not that everything is pointed at.

Fixtures are local and minimal (the package has no ``tests/__init__.py``, so a
dotted import of the milestone fixtures would not resolve without modifying
another test area — out of scope here). They mirror the small fake-backed setup
the workflow tests use: one query, N web sources, one claim per chunk, one
clustered verdict, one full-coverage finding, one report section.
"""

from __future__ import annotations

import asyncio

from app.agents.creator_packet import (
    CreatorPacketAgent,
    _HookDraft,
    _PacketOutput,
)
from app.agents.cross_verification import (
    CrossVerificationAgent,
    _VerdictDraft,
    _VerificationOutput,
)
from app.agents.editorial_critic import (
    EditorialCriticAgent,
    _CritiqueOutput,
    _IssueDraft,
)
from app.agents.evidence_extraction import (
    EvidenceExtractionAgent,
    _ExtractedClaim,
    _ExtractionOutput,
)
from app.agents.report import (
    ReportAgent,
    _ReportOutput,
    _SectionDraft,
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
from app.schemas.research_state import (
    CaveatKind,
    JobStatus,
    QualityIssueKind,
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

# --- Minimal local fake-backed deps (mirror tests/workflows, kept self-contained) ---


def _router(output: object, role: ModelRole = ModelRole.PLANNING) -> ModelRouter:
    """A one-shot router that replays a single scripted model output for ``role``."""
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={role: ModelChoice("fake", "fake-model")},
    )


def _planner(sub_question_texts: tuple[str, ...] = ("q1", "q2")) -> ResearchPlannerAgent:
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in sub_question_texts],
    )
    return ResearchPlannerAgent(_router(output))


def _discovery(n_sources: int = 2) -> SourceDiscoveryAgent:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
            for i in range(n_sources)
        ]
    )
    return SourceDiscoveryAgent(_router(output), search)


def _ingestion(n_sources: int = 2) -> IngestionService:
    by_url = {
        f"https://s{i}.com": FetchedContent(
            url=f"https://s{i}.com", content=f"<p>body {i}</p>".encode(), content_type="text/html"
        )
        for i in range(n_sources)
    }
    return IngestionService(FakeFetchProvider(by_url))


def _extractor(n_chunks: int = 2) -> EvidenceExtractionAgent:
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
    # The fake claims share the token "claim" → one cluster → one scripted call.
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


def _synth_out(statement: str) -> _SynthesisOutput:
    """A full-coverage synthesis output (covers S0-S4; out-of-range indices dropped)."""
    return _SynthesisOutput(
        findings=[
            _FindingDraft(
                statement=statement, supporting_verdicts=[0], sub_questions=[0, 1, 2, 3, 4]
            )
        ]
    )


def _synthesizer() -> SynthesisAgent:
    return SynthesisAgent(
        ModelRouter(
            providers={"fake": FakeProvider([_synth_out("finding")])},
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


def _deps(n_sources: int = 2) -> ResearchDeps:
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


def _run(deps: ResearchDeps | None = None, topic: str = "t") -> ResearchState:
    return asyncio.run(run_research(ResearchState(topic=topic), deps=deps or _deps()))


# --- Cross-band coherence: every band populated and mutually consistent ------


def _assert_provenance_resolves(final: ResearchState) -> None:
    """Assert the full Finding->Verdict->Evidence->Chunk->Source chain has no
    dangling ids. Direction is always referenced-id -> exists (clustering and
    coverage are lossy by design, so we never require the reverse)."""
    source_ids = {s.id for s in final.acquisition.sources}
    chunk_ids = {c.id for c in final.acquisition.chunks}
    evidence_ids = {e.id for e in final.acquisition.evidence}
    verdict_ids = {v.id for v in final.reasoning.verdicts}
    sub_question_ids = {sq.id for sq in final.plan.sub_questions}

    # Acquisition band: chunks point at real sources; evidence points at real
    # sources + chunks (and carries a url snapshot matching its source).
    sources_by_id = {s.id: s for s in final.acquisition.sources}
    for chunk in final.acquisition.chunks:
        assert chunk.source_id in source_ids, f"chunk {chunk.id} -> phantom source"
    for ev in final.acquisition.evidence:
        assert ev.source_id in source_ids, f"evidence {ev.id} -> phantom source"
        assert ev.chunk_id in chunk_ids, f"evidence {ev.id} -> phantom chunk"
        assert ev.source_url == sources_by_id[ev.source_id].url

    # Reasoning band: verdicts point at real evidence; findings point at real
    # verdicts + real sub-questions.
    for vd in final.reasoning.verdicts:
        for eid in vd.supporting_evidence_ids:
            assert eid in evidence_ids, f"verdict {vd.id} -> phantom evidence {eid}"
        for eid in vd.contradicting_evidence_ids:
            assert eid in evidence_ids, f"verdict {vd.id} -> phantom evidence {eid}"
    for finding in final.reasoning.synthesis.findings:
        for vid in finding.supporting_verdict_ids:
            assert vid in verdict_ids, f"finding {finding.id} -> phantom verdict {vid}"
        for sqid in finding.sub_question_ids:
            assert sqid in sub_question_ids, f"finding {finding.id} -> phantom sub-question {sqid}"


def test_full_pipeline_every_band_is_populated() -> None:
    # The whole pipeline ran: each band wrote its slice (the cross-band presence
    # check no single milestone test makes in one assertion).
    final = _run()
    assert final.status is JobStatus.COMPLETED
    assert final.error is None
    assert final.plan.sub_questions, "plan band empty"
    assert final.acquisition.sources, "acquisition sources empty"
    assert final.acquisition.chunks, "acquisition chunks empty"
    assert final.acquisition.evidence, "acquisition evidence empty"
    assert final.reasoning.verdicts, "reasoning verdicts empty"
    assert final.reasoning.synthesis.findings, "synthesis findings empty"
    assert final.reasoning.critiques, "reasoning critiques empty"
    assert final.publishing.reports, "publishing reports empty"


def test_full_pipeline_provenance_chain_resolves() -> None:
    # The cross-band invariant: Finding->Verdict->Evidence->Chunk->Source has no
    # dangling ids anywhere in the assembled corpus.
    _assert_provenance_resolves(_run())


def test_report_citations_resolve_against_acquisition_and_reasoning() -> None:
    # A published citation is code-resolved from the provenance chain: every id it
    # carries must exist upstream, and its url snapshot must match its source.
    final = _run()
    report = final.publishing.reports[0]
    source_ids = {s.id for s in final.acquisition.sources}
    sources_by_id = {s.id: s for s in final.acquisition.sources}
    evidence_ids = {e.id for e in final.acquisition.evidence}
    verdict_ids = {v.id for v in final.reasoning.verdicts}

    assert report.citations, "report produced no citations"
    for cit in report.citations:
        assert cit.source_id in source_ids, f"citation {cit.id} -> phantom source"
        assert cit.source_url == sources_by_id[cit.source_id].url
        for eid in cit.evidence_ids:
            assert eid in evidence_ids, f"citation {cit.id} -> phantom evidence {eid}"
        for vid in cit.verdict_ids:
            assert vid in verdict_ids, f"citation {cit.id} -> phantom verdict {vid}"


def test_report_sections_cite_only_real_findings_and_sub_questions() -> None:
    final = _run()
    report = final.publishing.reports[0]
    finding_ids = {f.id for f in final.reasoning.synthesis.findings}
    sub_question_ids = {sq.id for sq in final.plan.sub_questions}

    assert report.sections, "report produced no sections"
    for section in report.sections:
        for fid in section.finding_ids:
            assert fid in finding_ids, f"section {section.id} -> phantom finding {fid}"
        for sqid in section.sub_question_ids:
            assert sqid in sub_question_ids, f"section {section.id} -> phantom sub-question {sqid}"


def test_report_caveats_reflect_the_findings() -> None:
    # Caveats are code-derived from the full findings + last critique. On the
    # default happy path the lone verdict is SINGLE_SOURCE, so the finding is
    # weakly supported -> a WEAK_SUPPORT caveat must be present, and every caveat's
    # referenced ids must resolve upstream.
    final = _run()
    report = final.publishing.reports[0]
    finding_ids = {f.id for f in final.reasoning.synthesis.findings}
    sub_question_ids = {sq.id for sq in final.plan.sub_questions}
    critique_ids = {c.id for c in final.reasoning.critiques}

    assert report.caveats, "weakly-supported finding should yield a caveat"
    assert any(c.kind is CaveatKind.WEAK_SUPPORT for c in report.caveats)
    for cav in report.caveats:
        for fid in cav.finding_ids:
            assert fid in finding_ids, f"caveat -> phantom finding {fid}"
        for sqid in cav.sub_question_ids:
            assert sqid in sub_question_ids, f"caveat -> phantom sub-question {sqid}"
        if cav.critique_id is not None:
            assert cav.critique_id in critique_ids, "caveat -> phantom critique"


def test_full_pipeline_final_state_revalidates_strict() -> None:
    # The whole assembled state survives a strict (extra='forbid') round-trip.
    final = _run()
    assert ResearchState.model_validate(final.model_dump())


# --- Revision-loop E2E: cross-band invariants survive a loop-back ------------


_REVISE = _CritiqueOutput(
    issues=[_IssueDraft(kind=QualityIssueKind.UNCLEAR, detail="please clarify", findings=[0])],
    rationale="needs revision",
)
_ACCEPT = _CritiqueOutput(issues=[], rationale="sound")


def _loop_deps(
    synth_outputs: list[_SynthesisOutput], critic_outputs: list[_CritiqueOutput]
) -> ResearchDeps:
    """Deps whose synthesizer/critic replay the given scripts across the loop."""
    return ResearchDeps(
        planner=_planner(),
        discovery=_discovery(2),
        ingestion=_ingestion(2),
        extractor=_extractor(2),
        verifier=_verifier(),
        synthesizer=SynthesisAgent(
            ModelRouter(
                providers={"fake": FakeProvider(synth_outputs)},
                policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
            )
        ),
        critic=EditorialCriticAgent(
            ModelRouter(
                providers={"fake": FakeProvider(critic_outputs)},
                policy={ModelRole.PLANNING: ModelChoice("fake", "fake-model")},
            )
        ),
        reporter=_reporter(),
        strategist=_strategist(),
    )


def test_revise_then_accept_keeps_provenance_coherent() -> None:
    # revise -> re-synthesize -> accept -> report. The integration angle (vs. the
    # milestone test that only checks COMPLETED + call counts): after the loop-back
    # rewrites the synthesis, the full provenance chain + citations still resolve.
    deps = _loop_deps([_synth_out("first"), _synth_out("second")], [_REVISE, _ACCEPT])
    final = asyncio.run(run_research(ResearchState(topic="t"), deps=deps))

    assert final.status is JobStatus.COMPLETED
    assert final.revision_iteration == 2
    # The post-revision synthesis is the one wired into the report.
    assert final.reasoning.synthesis.findings[0].statement == "second"
    _assert_provenance_resolves(final)
    report = final.publishing.reports[0]
    finding_ids = {f.id for f in final.reasoning.synthesis.findings}
    for section in report.sections:
        for fid in section.finding_ids:
            assert fid in finding_ids, "post-revision report cites a phantom finding"


def test_cap_exhausted_completes_with_unresolved_critique_banner() -> None:
    # The critic always asks to REVISE; the cap forces termination. The run
    # COMPLETES (exhausted is not a failure), still publishes a report, and that
    # report carries the non-omittable unresolved-critique banner.
    deps = _loop_deps([_synth_out("first"), _synth_out("second")], [_REVISE, _REVISE])
    final = asyncio.run(run_research(ResearchState(topic="t"), deps=deps))

    assert final.status is JobStatus.COMPLETED
    assert final.revision_iteration == 2
    assert final.publishing.reports, "exhausted run should still publish a report"
    report = final.publishing.reports[0]
    assert any(c.kind is CaveatKind.UNRESOLVED_CRITIQUE for c in report.caveats)
    # Even on the exhausted path the provenance chain stays coherent.
    _assert_provenance_resolves(final)
