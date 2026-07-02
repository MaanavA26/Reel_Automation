"""Typed result DTOs for the post-render QC gate (ADR 0060).

A `QCReport` is the QC service's output contract: the ordered per-check findings
plus a code-derived `QCSummary`. It mirrors the `TTSQAReport` shape (a strict,
id-prefixed, `produced_via`-carrying media artifact) and the safety verdict's
"decision is the max severity over the reasons" derivation — but over a
**tri-state** status, because a QC check we could not run is *not* a pass.

The tri-state is the load-bearing distinction (council spine): `SKIPPED` ≠
`PASS`. A check whose input signal is unavailable (no word-level VO timing for
`FIRST_WORD_ONSET`, no recorded cut structure for `CUT_RHYTHM`) or that is
deliberately deferred (`CAPTION_SAFE_ZONE`, pending OCR) resolves to `SKIPPED`,
never a fabricated green. The overall status is the maximum over the per-check
statuses under the order ``PASS < SKIPPED < FAIL`` (mirroring `Severity` in
`safety/verdict.py`): all-pass → PASS; any SKIPPED with no FAIL → SKIPPED (the
render is *incomplete*, not verified); any FAIL → FAIL. This is pure: a report is
a value object fully determined by its findings (no clock).

Mapping a report to a publish decision (BLOCK/REVIEW/ALLOW) is **not** here — it
lives in the `QCGatePolicy` (`app.media.qc.gate`), keeping this report a raw
finding surface and the pure `PrePublishGate` (`safety/gate.py`) I/O-free.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.media.schemas import _gen_id

_STRICT = ConfigDict(extra="forbid")


class QCCheckStatus(IntEnum):
    """Tri-state outcome of a single QC check, ranked so the overall is a ``max``.

    An `IntEnum` (like `Severity`) so the report's overall status is
    ``max(...)`` over the per-check statuses. The ordering encodes the gate's
    core rule — **SKIPPED is not PASS**: a check we could not run ranks *above*
    a pass (so it cannot be hidden by passing siblings) but *below* a fail (a real
    failure still dominates the verdict).
    """

    PASS = 0
    SKIPPED = 1
    FAIL = 2

    @property
    def label(self) -> str:
        """The lowercase string form for logs/details (``"pass"``/``"skipped"``/``"fail"``)."""
        return self.name.lower()


class QCCheckKind(StrEnum):
    """Machine-readable identifier of a single Definition-of-Done QC check.

    Stable identifiers so a downstream surface (a publish UI, the QC gate policy)
    can branch on *which* check fired without re-parsing the human ``detail``.
    Each maps to one `QCCheckResult` in a report.
    """

    LENGTH_BAND = "length_band"  # total video length within the shorts band
    INTEGRATED_LOUDNESS = "integrated_loudness"  # measured LUFS within target ± tol
    TRUE_PEAK = "true_peak"  # measured true peak under the ceiling
    AUDIO_SAMPLE_RATE = "audio_sample_rate"  # output sample rate at/above the floor
    FIRST_WORD_ONSET = "first_word_onset"  # hook: first caption (+ first VO word) lands fast
    SCRIPT_PACE = "script_pace"  # post-render delivery pace within the wpm band
    CUT_RHYTHM = "cut_rhythm"  # no visual segment longer than the cut-gap ceiling
    CAPTION_PRESENCE = "caption_presence"  # caption track non-empty + covers the video
    CAPTIONS_BURNED_IN = "captions_burned_in"  # captions in pixels, not a soft track
    CAPTION_SAFE_ZONE = "caption_safe_zone"  # captions inside title-safe margin (OCR, deferred)


class QCCheckResult(BaseModel):
    """The outcome of a single QC check — a tri-state status plus a detail.

    Strict (`extra='forbid'`). ``detail`` always explains the verdict (the
    observed value vs. the rubric band, or *why* the check was skipped) so a
    report is self-describing for logs and the publish-decision policy.
    """

    model_config = _STRICT

    check: QCCheckKind
    status: QCCheckStatus
    detail: str


class QCSummary(BaseModel):
    """The code-derived rollup of a QC report's per-check findings.

    ``overall`` is the maximum status over the checks (``PASS < SKIPPED < FAIL``);
    the three counts are the partition of the checks by status. All four are
    derived from the findings by `QCReport.summarize` — the model never gets a
    vote. ``passed`` is the strict-green property: overall is exactly PASS (a
    SKIPPED check means the render is *not* fully verified, so it is not green).
    """

    model_config = _STRICT

    overall: QCCheckStatus
    passed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)

    @property
    def passed(self) -> bool:
        """True iff every check PASSed (overall is PASS — a SKIPPED is not green)."""
        return self.overall is QCCheckStatus.PASS


class QCReport(BaseModel):
    """The structured verdict of a QC pass over one `RenderedVideo` (ADR 0060).

    A strict, id-prefixed (`qcv_`) media artifact carrying a required
    `produced_via` provenance (e.g. ``"qc:deterministic"``), mirroring
    `TTSQAReport`. ``summary`` is **code-derived** from ``checks`` via
    `summarize` — the overall status and counts cannot disagree with the
    per-check findings. The full per-check breakdown is always retained so a
    caller sees *every* problem at once, not just the worst.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("qcv"))
    video_id: str
    summary: QCSummary
    checks: list[QCCheckResult]
    produced_via: str

    @staticmethod
    def summarize(checks: list[QCCheckResult]) -> QCSummary:
        """Derive the `QCSummary` from the per-check results (no checks → PASS).

        The overall is the maximum status over the checks; the counts partition
        them. Empty check list rolls up to PASS with zero counts (a degenerate
        but well-defined value), mirroring the safety gate's "no reasons → ALLOW".
        """
        passed = sum(1 for c in checks if c.status is QCCheckStatus.PASS)
        skipped = sum(1 for c in checks if c.status is QCCheckStatus.SKIPPED)
        failed = sum(1 for c in checks if c.status is QCCheckStatus.FAIL)
        overall = max((c.status for c in checks), default=QCCheckStatus.PASS)
        return QCSummary(
            overall=overall,
            passed_count=passed,
            skipped_count=skipped,
            failed_count=failed,
        )

    @property
    def failed_checks(self) -> list[QCCheckKind]:
        """The checks that FAILed — the publish policy's BLOCK/REVIEW signal."""
        return [c.check for c in self.checks if c.status is QCCheckStatus.FAIL]

    @property
    def skipped_checks(self) -> list[QCCheckKind]:
        """The checks that were SKIPPED — signals the render is not fully verified."""
        return [c.check for c in self.checks if c.status is QCCheckStatus.SKIPPED]
