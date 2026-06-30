"""Tests for the QC gate policy — findings -> publish decision (ADR 0060).

Mirrors the safety-gate test style. The policy maps a raw `QCReport` to a
`SafetyDecision` (reusing the safety vocabulary): FAIL -> REVIEW (or BLOCK if
hard-failed), SKIPPED -> REVIEW, all-PASS -> ALLOW.
"""

from __future__ import annotations

from app.media.qc.gate import QCGate, QCGatePolicy
from app.media.qc.report import QCCheckKind, QCCheckResult, QCCheckStatus, QCReport
from app.safety.verdict import SafetyDecision


def _report(*results: tuple[QCCheckKind, QCCheckStatus]) -> QCReport:
    checks = [QCCheckResult(check=k, status=s, detail="x") for k, s in results]
    return QCReport(
        video_id="vid_1",
        summary=QCReport.summarize(checks),
        checks=checks,
        produced_via="qc:deterministic",
    )


def test_all_pass_allows() -> None:
    report = _report(
        (QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        (QCCheckKind.TRUE_PEAK, QCCheckStatus.PASS),
    )
    verdict = QCGate().evaluate(report)
    assert verdict.decision is SafetyDecision.ALLOW
    assert verdict.allowed is True
    assert verdict.reasons == []


def test_a_skipped_check_routes_to_review() -> None:
    report = _report(
        (QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        (QCCheckKind.CAPTION_SAFE_ZONE, QCCheckStatus.SKIPPED),
    )
    verdict = QCGate().evaluate(report)
    assert verdict.decision is SafetyDecision.REVIEW
    assert [r.check for r in verdict.reasons] == [QCCheckKind.CAPTION_SAFE_ZONE]


def test_a_fail_routes_to_review_by_default() -> None:
    # CAPTIONS_BURNED_IN FAIL (today's hermetic state) -> REVIEW-locked.
    report = _report(
        (QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        (QCCheckKind.CAPTIONS_BURNED_IN, QCCheckStatus.FAIL),
    )
    verdict = QCGate().evaluate(report)
    assert verdict.decision is SafetyDecision.REVIEW


def test_hard_fail_check_escalates_to_block() -> None:
    policy = QCGatePolicy(hard_fail_checks=frozenset({QCCheckKind.INTEGRATED_LOUDNESS}))
    report = _report(
        (QCCheckKind.INTEGRATED_LOUDNESS, QCCheckStatus.FAIL),
        (QCCheckKind.CAPTION_SAFE_ZONE, QCCheckStatus.SKIPPED),
    )
    verdict = QCGate().evaluate(report)  # default: no hard fails -> REVIEW
    assert verdict.decision is SafetyDecision.REVIEW
    blocking = QCGate(policy).evaluate(report)  # loudness hard-failed -> BLOCK
    assert blocking.decision is SafetyDecision.BLOCK


def test_decision_is_max_severity_over_reasons() -> None:
    policy = QCGatePolicy(hard_fail_checks=frozenset({QCCheckKind.LENGTH_BAND}))
    report = _report(
        (QCCheckKind.LENGTH_BAND, QCCheckStatus.FAIL),  # -> BLOCK
        (QCCheckKind.CAPTION_SAFE_ZONE, QCCheckStatus.SKIPPED),  # -> REVIEW
    )
    verdict = QCGate(policy).evaluate(report)
    assert verdict.decision is SafetyDecision.BLOCK  # the max wins
    assert len(verdict.reasons) == 2  # both surfaced
