"""Tests for the deterministic caveat-derivation tool (M11).

Pins the §11 keystone: caveats are code-derived over the FULL findings set (so an
uncited disputed finding still surfaces), plus the latest critique's uncovered
sub-questions, quality issues, and the exhausted-revision banner.
"""

from __future__ import annotations

from app.schemas.research_state import (
    CaveatKind,
    Critique,
    CritiqueDecision,
    Finding,
    QualityIssue,
    QualityIssueKind,
    SupportLevel,
)
from app.services.publishing.caveats import derive_caveats


def _finding(
    *, disputed: bool = False, weakest: SupportLevel = SupportLevel.CORROBORATED
) -> Finding:
    return Finding(
        statement="f",
        supporting_verdict_ids=["vd_1"],
        disputed=disputed,
        weakest_support=weakest,
        synthesized_via="synthesis:fake",
    )


def test_disputed_finding_yields_caveat() -> None:
    f = _finding(disputed=True, weakest=SupportLevel.CONTRADICTED)
    caveats = derive_caveats([f], None)
    assert len(caveats) == 1
    assert caveats[0].kind is CaveatKind.DISPUTED_FINDING
    assert caveats[0].finding_ids == [f.id]


def test_single_source_finding_yields_weak_support_caveat() -> None:
    f = _finding(disputed=False, weakest=SupportLevel.SINGLE_SOURCE)
    caveats = derive_caveats([f], None)
    assert [c.kind for c in caveats] == [CaveatKind.WEAK_SUPPORT]


def test_corroborated_finding_yields_no_finding_caveat() -> None:
    assert derive_caveats([_finding()], None) == []


def test_uncovered_and_issues_and_exhausted_banner_from_critique() -> None:
    critique = Critique(
        decision=CritiqueDecision.REVISE,
        uncovered_sub_question_ids=["sq_9"],
        issues=[QualityIssue(kind=QualityIssueKind.OVERSTATED, detail="x", finding_ids=["fnd_1"])],
        rationale="r",
        critiqued_via="critique:fake",
    )
    caveats = derive_caveats([_finding()], critique)
    kinds = [c.kind for c in caveats]
    assert CaveatKind.UNCOVERED_SUB_QUESTION in kinds
    assert CaveatKind.QUALITY_ISSUE in kinds
    assert CaveatKind.UNRESOLVED_CRITIQUE in kinds  # decision == REVISE → exhausted banner
    banner = next(c for c in caveats if c.kind is CaveatKind.UNRESOLVED_CRITIQUE)
    assert banner.critique_id == critique.id


def test_accepted_critique_has_no_unresolved_banner() -> None:
    critique = Critique(
        decision=CritiqueDecision.ACCEPT, rationale="ok", critiqued_via="critique:fake"
    )
    caveats = derive_caveats([_finding()], critique)
    assert all(c.kind is not CaveatKind.UNRESOLVED_CRITIQUE for c in caveats)
