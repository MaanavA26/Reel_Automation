"""Tests for the QC report DTOs — tri-state rollup (ADR 0060).

The load-bearing rule: SKIPPED is not PASS. The overall status is the max over
the per-check statuses under PASS < SKIPPED < FAIL, and ``summary.passed`` is
strict-green (overall is exactly PASS).
"""

from __future__ import annotations

from app.media.qc.report import QCCheckKind, QCCheckResult, QCCheckStatus, QCReport


def _check(kind: QCCheckKind, status: QCCheckStatus) -> QCCheckResult:
    return QCCheckResult(check=kind, status=status, detail="x")


def test_all_pass_rolls_up_to_pass_and_is_green() -> None:
    checks = [
        _check(QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        _check(QCCheckKind.TRUE_PEAK, QCCheckStatus.PASS),
    ]
    summary = QCReport.summarize(checks)
    assert summary.overall is QCCheckStatus.PASS
    assert summary.passed is True
    assert (summary.passed_count, summary.skipped_count, summary.failed_count) == (2, 0, 0)


def test_skipped_is_not_pass() -> None:
    checks = [
        _check(QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        _check(QCCheckKind.CAPTION_SAFE_ZONE, QCCheckStatus.SKIPPED),
    ]
    summary = QCReport.summarize(checks)
    assert summary.overall is QCCheckStatus.SKIPPED
    assert summary.passed is False  # a SKIPPED check means not fully verified
    assert (summary.passed_count, summary.skipped_count, summary.failed_count) == (1, 1, 0)


def test_fail_dominates_skipped_and_pass() -> None:
    checks = [
        _check(QCCheckKind.LENGTH_BAND, QCCheckStatus.PASS),
        _check(QCCheckKind.CAPTION_SAFE_ZONE, QCCheckStatus.SKIPPED),
        _check(QCCheckKind.TRUE_PEAK, QCCheckStatus.FAIL),
    ]
    summary = QCReport.summarize(checks)
    assert summary.overall is QCCheckStatus.FAIL
    assert summary.passed is False
    assert (summary.passed_count, summary.skipped_count, summary.failed_count) == (1, 1, 1)


def test_empty_checks_roll_up_to_pass() -> None:
    summary = QCReport.summarize([])
    assert summary.overall is QCCheckStatus.PASS
    assert (summary.passed_count, summary.skipped_count, summary.failed_count) == (0, 0, 0)


def test_report_summary_is_code_derived_from_checks() -> None:
    checks = [
        _check(QCCheckKind.LENGTH_BAND, QCCheckStatus.FAIL),
        _check(QCCheckKind.CUT_RHYTHM, QCCheckStatus.SKIPPED),
    ]
    report = QCReport(
        video_id="vid_1",
        summary=QCReport.summarize(checks),
        checks=checks,
        produced_via="qc:deterministic",
    )
    assert report.failed_checks == [QCCheckKind.LENGTH_BAND]
    assert report.skipped_checks == [QCCheckKind.CUT_RHYTHM]
    assert report.summary.overall is QCCheckStatus.FAIL


def test_status_label() -> None:
    assert QCCheckStatus.PASS.label == "pass"
    assert QCCheckStatus.SKIPPED.label == "skipped"
    assert QCCheckStatus.FAIL.label == "fail"
