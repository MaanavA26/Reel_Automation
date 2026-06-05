"""Tests for the Deep Research state and provenance schema (ADR 0001)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Chunk,
    Citation,
    Critique,
    CritiqueDecision,
    Evidence,
    Finding,
    JobStatus,
    KnowledgeAcquisitionState,
    KnowledgeReasoningState,
    QualityIssue,
    QualityIssueKind,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchPublishingState,
    ResearchState,
    Source,
    SourceType,
    SubQuestion,
    SupportLevel,
    Synthesis,
    Verdict,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "research_state_minimal.json"


def test_research_state_defaults() -> None:
    state = ResearchState(topic="Quantum supremacy")

    assert state.topic == "Quantum supremacy"
    assert state.status == JobStatus.QUEUED
    assert state.id.startswith("job_")
    assert state.revision_iteration == 0
    assert isinstance(state.plan, ResearchPlan)
    assert state.plan.sub_questions == []
    assert isinstance(state.acquisition, KnowledgeAcquisitionState)
    assert state.acquisition.sources == []
    assert state.acquisition.chunks == []
    assert state.acquisition.evidence == []
    assert isinstance(state.reasoning, KnowledgeReasoningState)
    assert state.reasoning.verdicts == []
    assert isinstance(state.reasoning.synthesis, Synthesis)
    assert state.reasoning.synthesis.findings == []
    assert state.reasoning.critiques == []
    assert isinstance(state.publishing, ResearchPublishingState)
    assert state.publishing.reports == []


def test_report_roundtrips_under_strict_schema() -> None:
    # M11: a Report carries model prose + code-derived citations/caveats; it must
    # round-trip under extra="forbid".
    report = Report(
        title="T",
        abstract="A",
        sections=[ReportSection(heading="H", narrative="N", finding_ids=["fnd_1"])],
        citations=[Citation(source_id="src_1", source_url="https://x", source_type=SourceType.WEB)],
        caveats=[Caveat(kind=CaveatKind.WEAK_SUPPORT, detail="thin", finding_ids=["fnd_1"])],
        published_via="report:fake-model",
    )
    assert report.id.startswith("rpt_")
    assert Report.model_validate(report.model_dump()) == report


def test_critique_roundtrips_under_strict_schema() -> None:
    # M10: a Critique carries a code-derived decision + coverage and model-authored
    # issues (ids code-attached); it must round-trip under extra="forbid".
    critique = Critique(
        decision=CritiqueDecision.REVISE,
        uncovered_sub_question_ids=["sq_2"],
        issues=[
            QualityIssue(
                kind=QualityIssueKind.OVERSTATED,
                detail="overstates a disputed finding",
                finding_ids=["fnd_1"],
            )
        ],
        rationale="needs work",
        critiqued_via="critique:fake-model",
    )
    assert critique.id.startswith("crit_")
    assert Critique.model_validate(critique.model_dump()) == critique


def test_finding_roundtrips_under_strict_schema() -> None:
    # M9: a Finding references verdicts/sub-questions by id and carries the
    # code-derived grounding summary; it must round-trip under extra="forbid".
    finding = Finding(
        statement="synthesized finding",
        sub_question_ids=["sq_1"],
        supporting_verdict_ids=["vd_1", "vd_2"],
        disputed=True,
        weakest_support=SupportLevel.CONTRADICTED,
        synthesized_via="synthesis:fake-model",
    )
    assert finding.id.startswith("fnd_")
    assert Finding.model_validate(finding.model_dump()) == finding


def test_verdict_roundtrips_under_strict_schema() -> None:
    # M8: a Verdict references evidence by id (no inline snapshot) and carries
    # code-attached provenance; it must round-trip under extra="forbid".
    verdict = Verdict(
        claim="canonical claim",
        support_level=SupportLevel.CORROBORATED,
        supporting_evidence_ids=["ev_1", "ev_2"],
        contradicting_evidence_ids=[],
        confidence=0.9,
        verified_via="verification:fake-model",
    )
    assert verdict.id.startswith("vd_")
    rebuilt = Verdict.model_validate(verdict.model_dump())
    assert rebuilt == verdict


def test_verdict_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Verdict(
            claim="x",
            support_level=SupportLevel.SINGLE_SOURCE,
            confidence=1.5,
            verified_via="v",
        )


def test_id_format_prefixes() -> None:
    state = ResearchState(topic="t")
    plan = ResearchPlan()
    sub_q = SubQuestion(text="x")
    source = Source(url="https://x", type=SourceType.WEB, discovered_via="search:fake")
    chunk = Chunk(source_id=source.id, text="x")
    evidence = Evidence(
        claim="x",
        source_id=source.id,
        source_url=source.url,
        chunk_id=chunk.id,
        chunk_text="x",
        confidence=0.5,
        extracted_via="ext_v1",
    )

    assert state.id.startswith("job_")
    assert plan.id.startswith("plan_")
    assert sub_q.id.startswith("sq_")
    assert source.id.startswith("src_")
    assert chunk.id.startswith("chk_")
    assert evidence.id.startswith("ev_")


def test_confidence_out_of_range_rejected() -> None:
    base: dict[str, object] = {
        "claim": "x",
        "source_id": "src_a",
        "source_url": "https://x",
        "chunk_id": "chk_a",
        "chunk_text": "x",
        "extracted_via": "ext_v1",
    }
    with pytest.raises(ValidationError):
        Evidence(**base, confidence=1.1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Evidence(**base, confidence=-0.1)  # type: ignore[arg-type]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ResearchState.model_validate({"topic": "x", "spurious_field": "oops"})


def test_timestamps_are_timezone_aware() -> None:
    state = ResearchState(topic="t")
    assert state.created_at.tzinfo is not None
    assert state.updated_at.tzinfo is not None


def test_round_trip_json() -> None:
    state = _build_minimal_populated_state()

    blob = state.model_dump_json()
    parsed = ResearchState.model_validate_json(blob)

    assert parsed == state


def test_json_shape_matches_committed_fixture() -> None:
    """Canary against accidental schema-shape changes.

    Update the committed fixture deliberately when the schema legitimately
    evolves; that update is itself a reviewable signal of a backward-compat
    change.
    """
    fixture = json.loads(FIXTURE_PATH.read_text())

    state = ResearchState.model_validate(fixture)

    # Field equality against a programmatically-built reference state.
    reference = _build_minimal_populated_state()
    assert state == reference


def _build_minimal_populated_state() -> ResearchState:
    sub_q = SubQuestion(
        id="sq_fixed_001",
        text="When was the thing first claimed?",
        rationale="Establishes the timeline anchor for downstream claims.",
    )
    plan = ResearchPlan(
        id="plan_fixed_001",
        goal="Understand the timeline of the thing.",
        sub_questions=[sub_q],
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    source = Source(
        id="src_fixed_001",
        url="https://example.com/paper",
        type=SourceType.PAPER,
        discovered_via="search:fake",
        title="Example paper",
        discovered_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    chunk = Chunk(
        id="chk_fixed_001",
        source_id=source.id,
        text="The thing happened.",
        position=1,
    )
    evidence = Evidence(
        id="ev_fixed_001",
        claim="The thing happened.",
        source_id=source.id,
        source_url=source.url,
        chunk_id=chunk.id,
        chunk_text=chunk.text,
        confidence=0.9,
        extracted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        extracted_via="extraction_v1",
    )
    return ResearchState(
        id="job_fixed_001",
        topic="Example research topic",
        status=JobStatus.RUNNING,
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        plan=plan,
        acquisition=KnowledgeAcquisitionState(
            sources=[source],
            chunks=[chunk],
            evidence=[evidence],
        ),
    )
