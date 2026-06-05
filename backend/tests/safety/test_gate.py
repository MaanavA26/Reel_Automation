"""Tests for the deterministic pre-publish content-safety gate (ADR 0041).

Pins the publish-readiness policy: a clean report/packet/candidate is ALLOWed; a
report resting on a contradicted/disputed finding or an exhausted-critique banner
(without a disclaimer) is BLOCKed; a banned keyword and below-floor grounding are
flagged for human REVIEW. Hermetic — pure value objects, no LLM/IO/clock.
"""

from __future__ import annotations

from app.safety.gate import GatePolicy, PrePublishGate, PublishCandidate
from app.safety.verdict import SafetyDecision, SafetyReasonKind, Severity
from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Citation,
    CreatorPacket,
    CreatorWarning,
    Report,
    SourceType,
)


def _citation(source_id: str) -> Citation:
    return Citation(
        source_id=source_id,
        source_url=f"https://example.com/{source_id}",
        source_type=SourceType.WEB,
    )


def _report(
    *,
    caveats: list[Caveat] | None = None,
    n_distinct_sources: int = 2,
) -> Report:
    return Report(
        title="t",
        abstract="a",
        citations=[_citation(f"src_{i}") for i in range(n_distinct_sources)],
        caveats=caveats or [],
        published_via="report:fake",
    )


def _packet(*, warnings: list[CreatorWarning] | None = None) -> CreatorPacket:
    return CreatorPacket(
        report_id="rpt_1",
        warnings=warnings or [],
        published_via="packet:fake",
    )


def _candidate(*, disclaimer: str | None = None, text: str = "a clean script") -> PublishCandidate:
    return PublishCandidate(
        packet_id="pkt_1",
        title="A Safe Title",
        description="A safe description.",
        script_text=text,
        disclaimer=disclaimer,
    )


# -- ALLOW (clean) ----------------------------------------------------------


def test_clean_content_is_allowed() -> None:
    gate = PrePublishGate()
    verdict = gate.evaluate(_report(), _packet(), _candidate())
    assert verdict.decision is SafetyDecision.ALLOW
    assert verdict.reasons == []
    assert verdict.allowed is True


def test_benign_caveats_do_not_block() -> None:
    # WEAK_SUPPORT / UNCOVERED_SUB_QUESTION / QUALITY_ISSUE are NOT block signals.
    benign = [
        Caveat(kind=CaveatKind.WEAK_SUPPORT, detail="thin"),
        Caveat(kind=CaveatKind.UNCOVERED_SUB_QUESTION, detail="gap"),
        Caveat(kind=CaveatKind.QUALITY_ISSUE, detail="note"),
    ]
    verdict = PrePublishGate().evaluate(_report(caveats=benign), _packet(), _candidate())
    assert verdict.decision is SafetyDecision.ALLOW


# -- BLOCK (disputed / exhausted critique) ----------------------------------


def test_disputed_report_caveat_blocks() -> None:
    report = _report(
        caveats=[Caveat(kind=CaveatKind.DISPUTED_FINDING, detail="d", finding_ids=["fnd_1"])]
    )
    verdict = PrePublishGate().evaluate(report, _packet(), _candidate())
    assert verdict.decision is SafetyDecision.BLOCK
    assert any(r.kind is SafetyReasonKind.DISPUTED_FINDING for r in verdict.reasons)
    assert verdict.allowed is False


def test_disputed_packet_warning_blocks() -> None:
    packet = _packet(
        warnings=[
            CreatorWarning(kind=CaveatKind.DISPUTED_FINDING, detail="d", finding_ids=["fnd_1"])
        ]
    )
    verdict = PrePublishGate().evaluate(_report(), packet, _candidate())
    assert verdict.decision is SafetyDecision.BLOCK


def test_weak_support_packet_warning_does_not_block() -> None:
    # A WEAK_SUPPORT creator warning is not a disputed (contradicted) signal.
    packet = _packet(warnings=[CreatorWarning(kind=CaveatKind.WEAK_SUPPORT, detail="w")])
    verdict = PrePublishGate().evaluate(_report(), packet, _candidate())
    assert verdict.decision is SafetyDecision.ALLOW


def test_unresolved_critique_without_disclaimer_blocks() -> None:
    report = _report(caveats=[Caveat(kind=CaveatKind.UNRESOLVED_CRITIQUE, detail="exhausted")])
    verdict = PrePublishGate().evaluate(report, _packet(), _candidate(disclaimer=None))
    assert verdict.decision is SafetyDecision.BLOCK
    assert any(r.kind is SafetyReasonKind.UNRESOLVED_CRITIQUE for r in verdict.reasons)


def test_unresolved_critique_with_disclaimer_downgrades_to_review() -> None:
    report = _report(caveats=[Caveat(kind=CaveatKind.UNRESOLVED_CRITIQUE, detail="exhausted")])
    candidate = _candidate(disclaimer="These conclusions are provisional and unverified.")
    verdict = PrePublishGate().evaluate(report, _packet(), candidate)
    assert verdict.decision is SafetyDecision.REVIEW
    assert any(
        r.kind is SafetyReasonKind.UNRESOLVED_CRITIQUE and r.severity is Severity.REVIEW
        for r in verdict.reasons
    )


def test_blank_disclaimer_counts_as_no_disclaimer() -> None:
    report = _report(caveats=[Caveat(kind=CaveatKind.UNRESOLVED_CRITIQUE, detail="exhausted")])
    verdict = PrePublishGate().evaluate(report, _packet(), _candidate(disclaimer="   "))
    assert verdict.decision is SafetyDecision.BLOCK


# -- REVIEW (banned keyword) ------------------------------------------------


def test_banned_keyword_flags_review() -> None:
    gate = PrePublishGate(GatePolicy(banned_keywords=frozenset({"miracle cure"})))
    candidate = _candidate(text="This MIRACLE CURE will change everything.")
    verdict = gate.evaluate(_report(), _packet(), candidate)
    assert verdict.decision is SafetyDecision.REVIEW
    reasons = [r for r in verdict.reasons if r.kind is SafetyReasonKind.BANNED_KEYWORD]
    assert len(reasons) == 1


def test_banned_keyword_whole_word_avoids_substring_false_positive() -> None:
    # Default is whole-word: "thorpe" must not trip inside "Scunthorpe".
    gate = PrePublishGate(GatePolicy(banned_keywords=frozenset({"thorpe"})))
    verdict = gate.evaluate(_report(), _packet(), _candidate(text="Welcome to Scunthorpe"))
    assert verdict.decision is SafetyDecision.ALLOW


def test_banned_keyword_substring_mode_matches() -> None:
    gate = PrePublishGate(
        GatePolicy(banned_keywords=frozenset({"thorpe"}), banned_keyword_whole_word=False)
    )
    verdict = gate.evaluate(_report(), _packet(), _candidate(text="Welcome to Scunthorpe"))
    assert verdict.decision is SafetyDecision.REVIEW


def test_banned_keyword_severity_configurable_to_block() -> None:
    gate = PrePublishGate(
        GatePolicy(banned_keywords=frozenset({"forbidden"}), banned_keyword_severity=Severity.BLOCK)
    )
    verdict = gate.evaluate(_report(), _packet(), _candidate(text="a forbidden topic"))
    assert verdict.decision is SafetyDecision.BLOCK


def test_multiple_banned_keywords_yield_one_reason_each_sorted() -> None:
    gate = PrePublishGate(GatePolicy(banned_keywords=frozenset({"zeta", "alpha"})))
    verdict = gate.evaluate(_report(), _packet(), _candidate(text="alpha and zeta appear"))
    kinds = [r.kind for r in verdict.reasons]
    assert kinds.count(SafetyReasonKind.BANNED_KEYWORD) == 2
    # Sorted order: alpha before zeta.
    details = [r.detail for r in verdict.reasons if r.kind is SafetyReasonKind.BANNED_KEYWORD]
    assert "'alpha'" in details[0] and "'zeta'" in details[1]


# -- REVIEW (grounding floor) -----------------------------------------------


def test_insufficient_grounding_flags_review() -> None:
    verdict = PrePublishGate().evaluate(_report(n_distinct_sources=1), _packet(), _candidate())
    assert verdict.decision is SafetyDecision.REVIEW
    assert any(r.kind is SafetyReasonKind.INSUFFICIENT_GROUNDING for r in verdict.reasons)


def test_duplicate_citations_do_not_satisfy_distinct_floor() -> None:
    report = Report(
        title="t",
        abstract="a",
        citations=[_citation("src_1"), _citation("src_1")],  # same source twice
        published_via="report:fake",
    )
    verdict = PrePublishGate().evaluate(report, _packet(), _candidate())
    assert verdict.decision is SafetyDecision.REVIEW


def test_grounding_floor_disabled_when_threshold_zero() -> None:
    gate = PrePublishGate(GatePolicy(min_distinct_sources=0))
    verdict = gate.evaluate(_report(n_distinct_sources=0), _packet(), _candidate())
    assert verdict.decision is SafetyDecision.ALLOW


# -- precedence + explainability --------------------------------------------


def test_block_dominates_review_but_all_reasons_retained() -> None:
    # A disputed finding (BLOCK) + a banned keyword (REVIEW) + thin grounding (REVIEW).
    report = _report(
        caveats=[Caveat(kind=CaveatKind.DISPUTED_FINDING, detail="d", finding_ids=["fnd_1"])],
        n_distinct_sources=1,
    )
    gate = PrePublishGate(GatePolicy(banned_keywords=frozenset({"banned"})))
    verdict = gate.evaluate(report, _packet(), _candidate(text="banned word"))
    assert verdict.decision is SafetyDecision.BLOCK  # max severity wins
    kinds = {r.kind for r in verdict.reasons}
    assert kinds == {
        SafetyReasonKind.DISPUTED_FINDING,
        SafetyReasonKind.BANNED_KEYWORD,
        SafetyReasonKind.INSUFFICIENT_GROUNDING,
    }


def test_verdict_is_pure_value_object() -> None:
    gate = PrePublishGate()
    v1 = gate.evaluate(_report(), _packet(), _candidate())
    v2 = gate.evaluate(_report(), _packet(), _candidate())
    assert v1 == v2  # no id / timestamp — equal inputs, equal verdicts
