"""Tests for the deterministic descriptor-level TTS QA tool (ADR 0052).

Hermetic: builds `SynthesizedSpeech` descriptors directly (no audio, no model)
and asserts the per-check and overall verdicts. The QA service derives the
expected duration from the source text via a WPM model, so tests set the source
``text`` and the descriptor ``duration_ms`` independently to drive each check in
and out of band — they do not rely on any provider's char/word pacing.
"""

from __future__ import annotations

from app.media.schemas import SynthesizedSpeech
from app.media.tts.qa import (
    TTSQACheck,
    TTSQualityService,
    expected_duration_ms,
)


def _speech(
    *,
    duration_ms: int,
    audio_uri: str = "fake://a.wav",
    voice: str = "narrator",
) -> SynthesizedSpeech:
    return SynthesizedSpeech(
        audio_uri=audio_uri,
        duration_ms=duration_ms,
        voice=voice,
        produced_via="tts:fake",
    )


def test_expected_duration_from_word_count() -> None:
    # 150 words at 150 wpm == 1 minute == 60_000 ms.
    assert expected_duration_ms("word " * 150, words_per_minute=150.0) == 60_000
    assert expected_duration_ms("   ", words_per_minute=150.0) == 0
    assert expected_duration_ms("", words_per_minute=150.0) == 0


def test_passes_when_duration_within_tolerance_and_metadata_sane() -> None:
    qa = TTSQualityService(words_per_minute=150.0, duration_tolerance=0.6)
    # 30 words at 150 wpm => expected 12_000 ms; 12_000 is dead-on.
    text = "word " * 30
    report = qa.check(_speech(duration_ms=12_000), text=text)
    assert report.passed is True
    assert report.failed_checks == []
    assert report.expected_duration_ms == 12_000
    assert report.speech_id.startswith("aud_")
    assert report.id.startswith("qa_")
    assert report.produced_via == "tts-qa:descriptor"


def test_fails_on_zero_duration_audio() -> None:
    qa = TTSQualityService(words_per_minute=150.0)
    report = qa.check(_speech(duration_ms=0), text="some narration here")
    assert report.passed is False
    assert TTSQACheck.NON_EMPTY_AUDIO in report.failed_checks


def test_fails_on_truncated_audio_below_tolerance() -> None:
    qa = TTSQualityService(words_per_minute=150.0, duration_tolerance=0.5)
    # 30 words => expected 12_000 ms; tolerance band [6_000, 18_000]. 2_000 is far short.
    report = qa.check(_speech(duration_ms=2_000), text="word " * 30)
    assert report.passed is False
    assert TTSQACheck.DURATION_PLAUSIBLE in report.failed_checks
    # Non-empty audio still passed (it produced *something*, just too little).
    assert TTSQACheck.NON_EMPTY_AUDIO not in report.failed_checks


def test_fails_on_runaway_audio_above_tolerance() -> None:
    qa = TTSQualityService(words_per_minute=150.0, duration_tolerance=0.5)
    # 30 words => expected 12_000 ms; band [6_000, 18_000]. 60_000 is runaway/looping.
    report = qa.check(_speech(duration_ms=60_000), text="word " * 30)
    assert report.passed is False
    assert TTSQACheck.DURATION_PLAUSIBLE in report.failed_checks


def test_fails_on_blank_metadata() -> None:
    qa = TTSQualityService(words_per_minute=150.0)
    report = qa.check(_speech(duration_ms=12_000, audio_uri="   ", voice=""), text="word " * 30)
    assert report.passed is False
    assert TTSQACheck.SANE_METADATA in report.failed_checks


def test_empty_text_skips_duration_check() -> None:
    # An empty script has no plausible duration to compare against: the duration
    # check is skipped (auto-pass); only non-empty-audio guards the "nothing" case.
    qa = TTSQualityService(words_per_minute=150.0)
    report = qa.check(_speech(duration_ms=500), text="   ")
    assert report.expected_duration_ms == 0
    assert TTSQACheck.DURATION_PLAUSIBLE not in report.failed_checks


def test_tolerance_band_is_inclusive_of_bounds() -> None:
    qa = TTSQualityService(words_per_minute=150.0, duration_tolerance=0.5)
    # 30 words => expected 12_000 ms; band [6_000, 18_000]. Exactly on the bounds passes.
    assert qa.check(_speech(duration_ms=6_000), text="word " * 30).passed is True
    assert qa.check(_speech(duration_ms=18_000), text="word " * 30).passed is True
