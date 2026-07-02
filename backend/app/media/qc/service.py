"""The deterministic post-render QC evaluator (a tool, ADR 0060).

`QCService.evaluate` runs the machine-enforceable Definition-of-Done checks over a
finished `RenderedVideo` + its `CaptionTrack` + a `QCMeasurement` (the probe's
measurement of the rendered file), against a `QCRubric`, and returns a raw
`QCReport`. Per CLAUDE.md §4 this is pure **tool/service** work: every check is a
deterministic comparison over existing descriptor/measurement fields — no LLM, no
judgment, no I/O (the probe owns the one I/O seam; the evaluator is pure over its
handed-in `QCMeasurement`).

The report is **raw findings only**: per-check tri-state results plus a
code-derived `QCSummary`. Mapping those findings to a publish decision
(BLOCK/REVIEW/ALLOW) is the `QCGatePolicy`'s job (`app.media.qc.gate`), which keeps
the pure `PrePublishGate` (`safety/gate.py`) I/O-free.

SKIPPED ≠ PASS (the spine rule): a check whose signal is unavailable — no
word-level VO timing for the first-spoken-word half of `FIRST_WORD_ONSET`, no
recorded cut structure for `CUT_RHYTHM` — or that is deliberately deferred
(`CAPTION_SAFE_ZONE`, pending OCR; `CAPTIONS_BURNED_IN` until libass is
provisioned) resolves to SKIPPED or FAIL with a clear detail, **never** a
fabricated PASS.
"""

from __future__ import annotations

from app.media.qc.probe import QCMeasurement
from app.media.qc.report import QCCheckKind, QCCheckResult, QCCheckStatus, QCReport
from app.media.qc.rubric import QCRubric
from app.media.schemas import CaptionTrack, RenderedVideo


def _result(check: QCCheckKind, status: QCCheckStatus, detail: str) -> QCCheckResult:
    return QCCheckResult(check=check, status=status, detail=detail)


def _band(check: QCCheckKind, value: float, low: float, high: float, unit: str) -> QCCheckResult:
    """A PASS/FAIL inclusive-band check with a self-describing detail."""
    passed = low <= value <= high
    status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
    detail = f"{value:.3f}{unit} (band [{low:.3f}, {high:.3f}]{unit})" + (
        "" if passed else " — outside band"
    )
    return _result(check, status, detail)


class QCService:
    """Deterministic Definition-of-Done QC over a rendered video (no LLM, no I/O).

    Construct with an optional `QCRubric` (defaults to the shorts DoD; a
    per-channel skin passes a tuned rubric built from its JSON spec). ``evaluate``
    is pure: the same inputs always produce an equal report. The probe (the one
    I/O seam) measures the rendered file separately and the resulting
    `QCMeasurement` is handed in here.
    """

    name = "deterministic"

    def __init__(self, rubric: QCRubric | None = None) -> None:
        self._rubric = rubric if rubric is not None else QCRubric()

    @property
    def rubric(self) -> QCRubric:
        return self._rubric

    def evaluate(
        self,
        *,
        video: RenderedVideo,
        captions: CaptionTrack,
        measurement: QCMeasurement,
    ) -> QCReport:
        """Run every DoD check and return the raw `QCReport`.

        Runs all checks (does not short-circuit) so the report explains every
        problem at once. The summary (overall tri-state + counts) is code-derived
        from the checks via `QCReport.summarize`.
        """
        checks = [
            self._check_length_band(video),
            self._check_integrated_loudness(measurement),
            self._check_true_peak(measurement),
            self._check_audio_sample_rate(measurement),
            self._check_first_word_onset(captions),
            self._check_script_pace(video, captions),
            self._check_cut_rhythm(video),
            self._check_caption_presence(video, captions),
            self._check_captions_burned_in(captions, measurement),
            self._check_caption_safe_zone(),
        ]
        return QCReport(
            video_id=video.id,
            summary=QCReport.summarize(checks),
            checks=checks,
            produced_via=f"qc:{self.name}",
        )

    # -- length / audio mastering -------------------------------------------

    def _check_length_band(self, video: RenderedVideo) -> QCCheckResult:
        return _band(
            QCCheckKind.LENGTH_BAND,
            video.duration_ms / 1000,
            self._rubric.min_length_s,
            self._rubric.max_length_s,
            "s",
        )

    def _check_integrated_loudness(self, measurement: QCMeasurement) -> QCCheckResult:
        target = self._rubric.target_integrated_lufs
        tol = self._rubric.loudness_tolerance_lu
        return _band(
            QCCheckKind.INTEGRATED_LOUDNESS,
            measurement.loudness.input_i,
            target - tol,
            target + tol,
            " LUFS",
        )

    def _check_true_peak(self, measurement: QCMeasurement) -> QCCheckResult:
        """Measured true peak must be at/under the ceiling (one-sided)."""
        peak = measurement.loudness.input_tp
        ceiling = self._rubric.max_true_peak_dbtp
        passed = peak <= ceiling
        status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
        detail = f"{peak:.2f} dBTP (ceiling {ceiling:.2f} dBTP)" + (
            "" if passed else " — exceeds ceiling"
        )
        return _result(QCCheckKind.TRUE_PEAK, status, detail)

    def _check_audio_sample_rate(self, measurement: QCMeasurement) -> QCCheckResult:
        floor = self._rubric.min_audio_sample_rate_hz
        rate = measurement.audio_sample_rate_hz
        passed = rate >= floor
        status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
        detail = f"{rate} Hz (floor {floor} Hz)" + ("" if passed else " — below floor")
        return _result(QCCheckKind.AUDIO_SAMPLE_RATE, status, detail)

    # -- hook / pace --------------------------------------------------------

    def _check_first_word_onset(self, captions: CaptionTrack) -> QCCheckResult:
        """Hook: the first caption cue must start within the hook budget.

        The first-spoken-word half (first VO word ≤ rubric budget) is SKIPPED:
        `SynthesizedSpeech` carries only ``duration_ms``, no word-level timing, so
        there is no honest signal to measure (ADR 0060 — never a fabricated PASS).
        The caption part is a leading-gap regression guard: the first cue starts at
        ~0 by construction today, so this primarily catches a future regression
        that delays the opening caption, not a claim that the hook is "strong".
        """
        if not captions.cues:
            return _result(
                QCCheckKind.FIRST_WORD_ONSET,
                QCCheckStatus.SKIPPED,
                "no caption cues — nothing to measure first-cue onset against",
            )
        first_start_s = captions.cues[0].start_ms / 1000
        budget = self._rubric.max_first_caption_start_s
        passed = first_start_s <= budget
        status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
        detail = (
            f"first caption cue starts at {first_start_s:.3f}s (budget ≤{budget:.3f}s)"
            + ("" if passed else " — opening caption too late")
            + "; first-VO-word onset SKIPPED (no word-level VO timing available)"
        )
        return _result(QCCheckKind.FIRST_WORD_ONSET, status, detail)

    def _check_script_pace(self, video: RenderedVideo, captions: CaptionTrack) -> QCCheckResult:
        """Post-render delivery pace (words/sec) within the DoD band.

        Word source: the concatenated caption cue text (the on-screen narration);
        denominator: the *rendered* video duration. This is a **post-render
        delivery** check — distinct from TTS-QA's wide pre-compose plausibility
        band (different stage, tolerance, purpose; see ADR 0060). SKIPPED when
        there is no narration or zero duration to divide by (no honest pace).
        """
        words = sum(len(cue.text.split()) for cue in captions.cues)
        if words == 0 or video.duration_ms <= 0:
            return _result(
                QCCheckKind.SCRIPT_PACE,
                QCCheckStatus.SKIPPED,
                f"no narration words ({words}) or zero duration "
                f"({video.duration_ms}ms) — no pace to measure",
            )
        wps = words / (video.duration_ms / 1000)
        return _band(
            QCCheckKind.SCRIPT_PACE,
            wps,
            self._rubric.min_words_per_second,
            self._rubric.max_words_per_second,
            " wps",
        )

    # -- motion / captions --------------------------------------------------

    def _check_cut_rhythm(self, video: RenderedVideo) -> QCCheckResult:
        """No visual segment may run longer than the cut-gap ceiling.

        Ranges over `RenderedVideo.edit_list` (the deterministic per-visual
        segments the renderer recorded — not optical flow). The longest segment
        ``end - start`` must be ≤ the ceiling: a single full-length segment (a
        static image) therefore FAILs for any clip longer than the ceiling.
        SKIPPED when no edit list was recorded (cut structure unknown — not a
        fabricated PASS; e.g. the fake renderer / legacy descriptors).
        """
        if not video.edit_list:
            return _result(
                QCCheckKind.CUT_RHYTHM,
                QCCheckStatus.SKIPPED,
                "no edit_list recorded — cut structure unknown",
            )
        max_gap_ms = max(end - start for start, end in video.edit_list)
        ceiling_ms = self._rubric.max_cut_gap_s * 1000
        passed = max_gap_ms <= ceiling_ms
        status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
        detail = (
            f"longest visual segment {max_gap_ms / 1000:.3f}s over "
            f"{len(video.edit_list)} segment(s) (ceiling ≤{self._rubric.max_cut_gap_s:.3f}s)"
            + ("" if passed else " — static/under-cut")
        )
        return _result(QCCheckKind.CUT_RHYTHM, status, detail)

    def _check_caption_presence(
        self, video: RenderedVideo, captions: CaptionTrack
    ) -> QCCheckResult:
        """Caption track non-empty AND covers a minimum fraction of the video.

        Coverage is the summed cue span ÷ the **video** duration (not the track's
        own span — that is always 1.0 and could never fail, the tautology the
        brief tells us to drop). A track that ends well before the video FAILs.
        FAIL (not SKIP) when empty: a short-form video with no captions is a hard
        DoD miss, a real failure we *can* measure.
        """
        if not captions.cues:
            return _result(
                QCCheckKind.CAPTION_PRESENCE,
                QCCheckStatus.FAIL,
                "caption track is empty — burned-in captions are a DoD requirement",
            )
        if video.duration_ms <= 0:
            return _result(
                QCCheckKind.CAPTION_PRESENCE,
                QCCheckStatus.SKIPPED,
                "zero video duration — no coverage denominator",
            )
        covered_ms = sum(max(0, cue.end_ms - cue.start_ms) for cue in captions.cues)
        coverage = covered_ms / video.duration_ms
        floor = self._rubric.min_caption_coverage
        passed = coverage >= floor
        status = QCCheckStatus.PASS if passed else QCCheckStatus.FAIL
        detail = (
            f"{len(captions.cues)} cue(s) cover {coverage:.1%} of the video "
            f"(floor ≥{floor:.0%})" + ("" if passed else " — captions cover too little")
        )
        return _result(QCCheckKind.CAPTION_PRESENCE, status, detail)

    def _check_captions_burned_in(
        self, captions: CaptionTrack, measurement: QCMeasurement
    ) -> QCCheckResult:
        """Captions must be burned into the pixels, not muxed as a soft track.

        Signal: a present soft subtitle stream in the rendered file is the
        signature of the libass-absent soft-mux fallback → captions are **not**
        burned in → FAIL. No soft subtitle stream (and a non-empty track) → the
        captions went into the pixels → PASS. This FAILs hermetically today (this
        sandbox has no libass, so the renderer soft-muxes), which is correct: it
        keeps autonomous mode REVIEW-locked until libass lands (spine C4). It
        verifies the *absence of the soft-mux signature*, not literal pixels
        (pixel/OCR verification is deferred — see `CAPTION_SAFE_ZONE`).
        """
        if not captions.cues:
            return _result(
                QCCheckKind.CAPTIONS_BURNED_IN,
                QCCheckStatus.FAIL,
                "no caption cues to burn in",
            )
        if measurement.has_soft_subtitle_stream:
            return _result(
                QCCheckKind.CAPTIONS_BURNED_IN,
                QCCheckStatus.FAIL,
                "rendered file carries a soft subtitle stream (libass-absent soft-mux "
                "fallback) — captions are not burned into the pixels",
            )
        return _result(
            QCCheckKind.CAPTIONS_BURNED_IN,
            QCCheckStatus.PASS,
            "no soft subtitle stream — captions are burned into the pixels",
        )

    @staticmethod
    def _check_caption_safe_zone() -> QCCheckResult:
        """Title-safe placement of captions — SKIPPED (OCR deferred, ADR 0060).

        Verifying captions sit inside the title-safe margin needs pixel/OCR
        analysis (tesseract), a dependency the owner deferred (decision D3). Rather
        than fake a PASS, this resolves to SKIPPED with a clear deferral detail;
        the QC gate policy routes a SKIPPED safe-zone to human REVIEW.
        """
        return QCCheckResult(
            check=QCCheckKind.CAPTION_SAFE_ZONE,
            status=QCCheckStatus.SKIPPED,
            detail="title-safe OCR check deferred (no tesseract dependency) — routed to REVIEW",
        )
