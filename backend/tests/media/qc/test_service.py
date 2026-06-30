"""Tests for the deterministic QC evaluator (ADR 0060).

Each check is exercised at its rubric boundary (PASS just inside, FAIL just
outside), the tri-state SKIPPED cases are asserted distinct from PASS, and a
per-channel rubric override is shown to flip a verdict. The evaluator is pure
over a handed-in `QCMeasurement` — no binary.
"""

from __future__ import annotations

import pytest

from app.media.composition.loudness import LoudnessStats
from app.media.qc.probe import QCMeasurement
from app.media.qc.report import QCCheckKind, QCCheckResult, QCCheckStatus
from app.media.qc.rubric import QCRubric
from app.media.qc.service import QCService
from app.media.schemas import Caption, CaptionTrack, RenderedVideo


def _measurement(
    *,
    integrated: float = -14.0,
    true_peak: float = -1.5,
    sample_rate: int = 44100,
    soft_sub: bool = False,
) -> QCMeasurement:
    return QCMeasurement(
        loudness=LoudnessStats(
            input_i=integrated,
            input_tp=true_peak,
            input_lra=7.0,
            input_thresh=-24.0,
            target_offset=0.0,
        ),
        audio_sample_rate_hz=sample_rate,
        has_soft_subtitle_stream=soft_sub,
    )


def _video(
    *,
    duration_ms: int = 60_000,
    edit_list: list[tuple[int, int]] | None = None,
) -> RenderedVideo:
    return RenderedVideo(
        id="vid_test",
        video_uri="file:///tmp/out.mp4",
        duration_ms=duration_ms,
        width=1080,
        height=1920,
        produced_via="composition:fake",
        edit_list=[] if edit_list is None else edit_list,
    )


def _captions(cues: list[Caption] | None = None) -> CaptionTrack:
    if cues is None:
        # ~150 words over a 60s clip = 2.5 wps (inside 140-170 wpm), full coverage.
        words = " ".join(["word"] * 25)
        cues = [Caption(start_ms=i * 10_000, end_ms=(i + 1) * 10_000, text=words) for i in range(6)]
    return CaptionTrack(cues=cues, produced_via="subtitles:deterministic")


def _find(checks: list[QCCheckResult], kind: QCCheckKind) -> QCCheckResult:
    return next(c for c in checks if c.check == kind)


def _status(
    kind: QCCheckKind,
    *,
    video: RenderedVideo | None = None,
    captions: CaptionTrack | None = None,
    measurement: QCMeasurement | None = None,
    rubric: QCRubric | None = None,
) -> QCCheckStatus:
    report = QCService(rubric).evaluate(
        video=video if video is not None else _video(edit_list=[(0, 30_000), (30_000, 60_000)]),
        captions=captions if captions is not None else _captions(),
        measurement=measurement if measurement is not None else _measurement(),
    )
    return _find(report.checks, kind).status


# --- LENGTH_BAND -----------------------------------------------------------


def test_length_band_pass_at_lower_boundary() -> None:
    assert _status(QCCheckKind.LENGTH_BAND, video=_video(duration_ms=45_000)) is QCCheckStatus.PASS


def test_length_band_fail_below_band() -> None:
    assert _status(QCCheckKind.LENGTH_BAND, video=_video(duration_ms=44_000)) is QCCheckStatus.FAIL


def test_length_band_fail_above_band() -> None:
    assert _status(QCCheckKind.LENGTH_BAND, video=_video(duration_ms=91_000)) is QCCheckStatus.FAIL


# --- INTEGRATED_LOUDNESS / TRUE_PEAK / SAMPLE_RATE (handed-in stats) --------


def test_integrated_loudness_pass_at_tolerance_edge() -> None:
    # target -14 ±1 -> -15.0 is the inclusive lower edge.
    assert (
        _status(QCCheckKind.INTEGRATED_LOUDNESS, measurement=_measurement(integrated=-15.0))
        is QCCheckStatus.PASS
    )


def test_integrated_loudness_fail_too_quiet() -> None:
    assert (
        _status(QCCheckKind.INTEGRATED_LOUDNESS, measurement=_measurement(integrated=-22.0))
        is QCCheckStatus.FAIL
    )


def test_true_peak_pass_at_ceiling() -> None:
    assert (
        _status(QCCheckKind.TRUE_PEAK, measurement=_measurement(true_peak=-1.0))
        is QCCheckStatus.PASS
    )


def test_true_peak_fail_over_ceiling() -> None:
    assert (
        _status(QCCheckKind.TRUE_PEAK, measurement=_measurement(true_peak=-0.5))
        is QCCheckStatus.FAIL
    )


def test_sample_rate_pass_at_floor() -> None:
    assert (
        _status(QCCheckKind.AUDIO_SAMPLE_RATE, measurement=_measurement(sample_rate=44100))
        is QCCheckStatus.PASS
    )


def test_sample_rate_fail_below_floor() -> None:
    assert (
        _status(QCCheckKind.AUDIO_SAMPLE_RATE, measurement=_measurement(sample_rate=24000))
        is QCCheckStatus.FAIL
    )


# --- FIRST_WORD_ONSET ------------------------------------------------------


def test_first_word_onset_pass_when_first_cue_is_prompt() -> None:
    cues = [Caption(start_ms=0, end_ms=60_000, text=" ".join(["w"] * 150))]
    assert _status(QCCheckKind.FIRST_WORD_ONSET, captions=_captions(cues)) is QCCheckStatus.PASS


def test_first_word_onset_fail_when_first_cue_too_late() -> None:
    cues = [Caption(start_ms=3000, end_ms=60_000, text=" ".join(["w"] * 150))]
    assert _status(QCCheckKind.FIRST_WORD_ONSET, captions=_captions(cues)) is QCCheckStatus.FAIL


def test_first_word_onset_skipped_when_no_cues() -> None:
    assert _status(QCCheckKind.FIRST_WORD_ONSET, captions=_captions([])) is QCCheckStatus.SKIPPED


def test_first_word_onset_detail_notes_vo_skip() -> None:
    cues = [Caption(start_ms=0, end_ms=60_000, text="hi")]
    report = QCService().evaluate(
        video=_video(edit_list=[(0, 60_000)]),
        captions=_captions(cues),
        measurement=_measurement(),
    )
    detail = _find(report.checks, QCCheckKind.FIRST_WORD_ONSET).detail
    assert "first-VO-word onset SKIPPED" in detail


# --- SCRIPT_PACE -----------------------------------------------------------


def test_script_pace_pass_in_band() -> None:
    # 150 words over 60s = 2.5 wps (within 140-170 wpm).
    cues = [Caption(start_ms=0, end_ms=60_000, text=" ".join(["w"] * 150))]
    assert _status(QCCheckKind.SCRIPT_PACE, captions=_captions(cues)) is QCCheckStatus.PASS


def test_script_pace_fail_too_fast() -> None:
    # 300 words over 60s = 5 wps (way over 170 wpm).
    cues = [Caption(start_ms=0, end_ms=60_000, text=" ".join(["w"] * 300))]
    assert _status(QCCheckKind.SCRIPT_PACE, captions=_captions(cues)) is QCCheckStatus.FAIL


def test_script_pace_skipped_when_no_words() -> None:
    cues = [Caption(start_ms=0, end_ms=60_000, text="   ")]
    assert _status(QCCheckKind.SCRIPT_PACE, captions=_captions(cues)) is QCCheckStatus.SKIPPED


# --- CUT_RHYTHM ------------------------------------------------------------


def test_cut_rhythm_pass_with_tight_segments() -> None:
    # 60s in 30 equal 2s segments — all under the 3s ceiling.
    edit_list = [(i * 2000, (i + 1) * 2000) for i in range(30)]
    assert _status(QCCheckKind.CUT_RHYTHM, video=_video(edit_list=edit_list)) is QCCheckStatus.PASS


def test_cut_rhythm_fail_with_a_long_gap() -> None:
    # One 4s segment exceeds the 3s ceiling.
    edit_list = [(0, 4000), (4000, 60_000)]
    assert _status(QCCheckKind.CUT_RHYTHM, video=_video(edit_list=edit_list)) is QCCheckStatus.FAIL


def test_cut_rhythm_fail_for_static_single_segment() -> None:
    # A static image -> one full-length segment -> max gap = full duration -> FAIL.
    assert (
        _status(QCCheckKind.CUT_RHYTHM, video=_video(edit_list=[(0, 60_000)])) is QCCheckStatus.FAIL
    )


def test_cut_rhythm_skipped_when_no_edit_list() -> None:
    assert _status(QCCheckKind.CUT_RHYTHM, video=_video(edit_list=[])) is QCCheckStatus.SKIPPED


# --- CAPTION_PRESENCE ------------------------------------------------------


def test_caption_presence_fail_when_empty() -> None:
    assert _status(QCCheckKind.CAPTION_PRESENCE, captions=_captions([])) is QCCheckStatus.FAIL


def test_caption_presence_fail_on_low_coverage() -> None:
    # Captions cover only 10s of a 60s video -> below the 50% floor.
    cues = [Caption(start_ms=0, end_ms=10_000, text="hi")]
    assert _status(QCCheckKind.CAPTION_PRESENCE, captions=_captions(cues)) is QCCheckStatus.FAIL


def test_caption_presence_pass_on_full_coverage() -> None:
    cues = [Caption(start_ms=0, end_ms=60_000, text="hi")]
    assert _status(QCCheckKind.CAPTION_PRESENCE, captions=_captions(cues)) is QCCheckStatus.PASS


# --- CAPTIONS_BURNED_IN (the C4 hermetic-FAIL guard) -----------------------


def test_captions_burned_in_fails_when_soft_track_present() -> None:
    # The libass-absent soft-mux fallback (today's hermetic path).
    assert (
        _status(QCCheckKind.CAPTIONS_BURNED_IN, measurement=_measurement(soft_sub=True))
        is QCCheckStatus.FAIL
    )


def test_captions_burned_in_passes_when_no_soft_track() -> None:
    # Would PASS once libass lands and captions are in the pixels.
    assert (
        _status(QCCheckKind.CAPTIONS_BURNED_IN, measurement=_measurement(soft_sub=False))
        is QCCheckStatus.PASS
    )


# --- CAPTION_SAFE_ZONE (OCR deferred) --------------------------------------


def test_caption_safe_zone_is_skipped_by_design() -> None:
    report = QCService().evaluate(
        video=_video(edit_list=[(0, 60_000)]),
        captions=_captions(),
        measurement=_measurement(),
    )
    result = _find(report.checks, QCCheckKind.CAPTION_SAFE_ZONE)
    assert result.status is QCCheckStatus.SKIPPED
    assert "deferred" in result.detail.lower()


# --- per-channel rubric override flips a verdict ----------------------------


def test_per_channel_rubric_override_changes_a_verdict() -> None:
    long_video = _video(duration_ms=120_000, edit_list=[(0, 60_000), (60_000, 120_000)])
    # Default band caps at 90s -> a 120s clip FAILs LENGTH_BAND.
    assert _status(QCCheckKind.LENGTH_BAND, video=long_video) is QCCheckStatus.FAIL
    # A channel skin that allows long-form (band to 180s) -> same clip PASSes.
    tuned = QCRubric.from_mapping({"max_length_s": 180.0})
    assert _status(QCCheckKind.LENGTH_BAND, video=long_video, rubric=tuned) is QCCheckStatus.PASS


# --- overall summary is code-derived ---------------------------------------


def test_overall_summary_is_code_derived_from_checks() -> None:
    report = QCService().evaluate(
        video=_video(edit_list=[(0, 30_000), (30_000, 60_000)]),
        captions=_captions(),
        measurement=_measurement(soft_sub=True),  # forces CAPTIONS_BURNED_IN FAIL
    )
    # Real FAIL present -> overall FAIL regardless of the SKIPPED safe-zone.
    assert report.summary.overall is QCCheckStatus.FAIL
    assert QCCheckKind.CAPTIONS_BURNED_IN in report.failed_checks
    assert QCCheckKind.CAPTION_SAFE_ZONE in report.skipped_checks
    # Counts partition the checks.
    total = report.summary.passed_count + report.summary.skipped_count + report.summary.failed_count
    assert total == len(report.checks)


def test_report_provenance() -> None:
    report = QCService().evaluate(
        video=_video(edit_list=[(0, 60_000)]),
        captions=_captions(),
        measurement=_measurement(),
    )
    assert report.produced_via == "qc:deterministic"
    assert report.video_id == "vid_test"


def test_evaluate_is_pure() -> None:
    service = QCService()
    args = dict(
        video=_video(edit_list=[(0, 30_000), (30_000, 60_000)]),
        captions=_captions(),
        measurement=_measurement(),
    )
    first = service.evaluate(**args)  # type: ignore[arg-type]
    second = service.evaluate(**args)  # type: ignore[arg-type]
    # Equal but for the random report id; compare the checks + summary.
    assert first.checks == second.checks
    assert first.summary == second.summary


def test_unused_rubric_field_is_referenced() -> None:
    # max_first_vo_word_s is the (deferred) VO-onset budget; assert it stays on
    # the rubric so the deferred check has its constant when word timing lands.
    assert QCRubric().max_first_vo_word_s == pytest.approx(1.0)
