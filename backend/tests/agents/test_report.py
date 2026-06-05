"""Tests for the Report agent (M11).

Hermetic: a `FakeProvider` scripts one `_ReportOutput`. The tests pin the
contract: section ids are code-attached from the real findings, the bibliography
is code-derived from the provenance chain (the model authors no url), caveats are
code-derived over the FULL findings set (an uncited disputed finding still
surfaces), an exhausted critique surfaces a banner, and empty/zero-section raise.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.report import (
    ReportAgent,
    ReportError,
    _ReportOutput,
    _SectionDraft,
)
from app.schemas.research_state import (
    CaveatKind,
    Critique,
    CritiqueDecision,
    Evidence,
    Finding,
    KnowledgeAcquisitionState,
    KnowledgeReasoningState,
    Source,
    SourceType,
    SupportLevel,
    Synthesis,
    Verdict,
)
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _agent(outputs: list[_ReportOutput]) -> tuple[ReportAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "long-context-model")},
    )
    return ReportAgent(router), fake


def _grounded() -> tuple[KnowledgeReasoningState, KnowledgeAcquisitionState]:
    """One corroborated finding fully grounded through verdict → evidence → source."""
    source = Source(
        id="src_a", url="https://src_a.com", type=SourceType.WEB, discovered_via="search:fake"
    )
    evidence = Evidence(
        id="ev_1",
        claim="c",
        source_id="src_a",
        source_url="https://src_a.com",
        chunk_id="chk_1",
        chunk_text="t",
        confidence=0.8,
        extracted_via="extraction:fake",
    )
    verdict = Verdict(
        id="vd_1",
        claim="c",
        support_level=SupportLevel.CORROBORATED,
        supporting_evidence_ids=["ev_1"],
        confidence=0.8,
        verified_via="verification:fake",
    )
    finding = Finding(
        statement="solar capacity grew sharply",
        supporting_verdict_ids=["vd_1"],
        disputed=False,
        weakest_support=SupportLevel.CORROBORATED,
        synthesized_via="synthesis:fake",
    )
    reasoning = KnowledgeReasoningState(verdicts=[verdict], synthesis=Synthesis(findings=[finding]))
    acquisition = KnowledgeAcquisitionState(sources=[source], evidence=[evidence])
    return reasoning, acquisition


def _plan() -> object:
    from app.schemas.research_state import ResearchPlan, SubQuestion

    return ResearchPlan(goal="goal", sub_questions=[SubQuestion(text="q")])


def _section(findings: list[int], heading: str = "Overview") -> _SectionDraft:
    return _SectionDraft(heading=heading, narrative="prose", findings=findings)


def _out(sections: list[_SectionDraft]) -> _ReportOutput:
    return _ReportOutput(title="Title", abstract="Abstract", sections=sections)


def test_builds_report_with_code_attached_section_ids() -> None:
    reasoning, acquisition = _grounded()
    agent, _ = _agent([_out([_section([0])])])
    report = asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert report.id.startswith("rpt_")
    assert report.published_via == "report:long-context-model"
    assert report.title == "Title"
    assert report.sections[0].finding_ids == [reasoning.synthesis.findings[0].id]


def test_uses_long_context_role_and_finding_in_prompt() -> None:
    reasoning, acquisition = _grounded()
    agent, fake = _agent([_out([_section([0])])])
    asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert fake.calls[0].model == "long-context-model"
    assert "solar capacity grew sharply" in fake.calls[0].prompt


def test_bibliography_is_code_derived() -> None:
    # The model output carries no url; the report's citations still list the real
    # source, walked from the provenance chain.
    reasoning, acquisition = _grounded()
    agent, _ = _agent([_out([_section([0])])])
    report = asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert [c.source_url for c in report.citations] == ["https://src_a.com"]


def test_fabricated_finding_index_is_dropped() -> None:
    reasoning, acquisition = _grounded()
    agent, _ = _agent([_out([_section([0, 9])])])
    report = asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert report.sections[0].finding_ids == [reasoning.synthesis.findings[0].id]


def test_section_with_only_fabricated_findings_dropped_then_raises() -> None:
    reasoning, acquisition = _grounded()
    agent, _ = _agent([_out([_section([9])])])
    with pytest.raises(ReportError):
        asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]


def test_caveats_cover_uncited_disputed_finding() -> None:
    # THE KEYSTONE: a disputed finding the model does NOT cite still produces a
    # caveat — caveats range over the full synthesis, not the cited subset.
    reasoning, acquisition = _grounded()
    disputed = Finding(
        statement="contested claim",
        supporting_verdict_ids=["vd_1"],
        disputed=True,
        weakest_support=SupportLevel.CONTRADICTED,
        synthesized_via="synthesis:fake",
    )
    reasoning.synthesis.findings.append(disputed)  # index 1, NOT cited below
    agent, _ = _agent([_out([_section([0])])])  # section cites only the clean finding
    report = asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert any(
        c.kind is CaveatKind.DISPUTED_FINDING and disputed.id in c.finding_ids
        for c in report.caveats
    )


def test_exhausted_critique_surfaces_banner_and_does_not_raise() -> None:
    reasoning, acquisition = _grounded()
    reasoning.critiques.append(
        Critique(
            decision=CritiqueDecision.REVISE, rationale="unsatisfied", critiqued_via="critique:fake"
        )
    )
    agent, _ = _agent([_out([_section([0])])])
    report = asyncio.run(agent.generate(_plan(), reasoning, acquisition))  # type: ignore[arg-type]
    assert any(c.kind is CaveatKind.UNRESOLVED_CRITIQUE for c in report.caveats)


def test_empty_findings_raises() -> None:
    agent, _ = _agent([_out([_section([0])])])
    with pytest.raises(ReportError):
        asyncio.run(
            agent.generate(_plan(), KnowledgeReasoningState(), KnowledgeAcquisitionState())  # type: ignore[arg-type]
        )
