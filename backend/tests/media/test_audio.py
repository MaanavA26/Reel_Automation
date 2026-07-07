"""Tests for the shared audio primitives (`app.media.audio`, ADR 0067).

Fully offline/hermetic: pure functions over in-memory bytes/sample lists, plus
`read_wav_clip` against real ``tmp_path`` files. These primitives were
extracted verbatim from `SegmentedTTSProvider` (#150/ADR 0064) — that
provider's own suite (`tests/media/tts/test_segmented.py`) still exercises
them end-to-end through its unchanged public API; this suite covers the shared
functions directly (including callers `segmented.py` never had, like a
zero-length pause).
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from app.media.audio import (
    DEFAULT_PAUSE_MS,
    AudioProcessingError,
    decode_wav_pcm16,
    make_silence,
    read_wav_clip,
    silence_sample_count,
    splice_with_pauses,
)
from app.media.tts.kokoro import encode_wav_pcm16

_SAMPLE_RATE = 8000


# --- decode_wav_pcm16 ---------------------------------------------------------


def test_decode_is_the_exact_inverse_of_encode() -> None:
    # Amplitudes chosen to be exactly representable after the int16 round trip
    # is approximated: assert to within one quantization step (1/32767).
    samples = [0.0, 0.5, -0.5, 1.0, -1.0, 0.25]
    decoded, rate = decode_wav_pcm16(encode_wav_pcm16(samples, _SAMPLE_RATE))
    assert rate == _SAMPLE_RATE
    assert len(decoded) == len(samples)
    for original, roundtripped in zip(samples, decoded, strict=True):
        assert roundtripped == pytest.approx(original, abs=1 / 32767)


def test_decode_rejects_stereo_with_clear_error() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_out:
        wav_out.setnchannels(2)  # unsupported: the decode contract is mono
        wav_out.setsampwidth(2)
        wav_out.setframerate(_SAMPLE_RATE)
        wav_out.writeframes(b"\x00\x00\x00\x00")
    with pytest.raises(AudioProcessingError, match="mono 16-bit PCM"):
        decode_wav_pcm16(buffer.getvalue())


def test_decode_rejects_non_16_bit_with_clear_error() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(1)  # 8-bit: unsupported
        wav_out.setframerate(_SAMPLE_RATE)
        wav_out.writeframes(b"\x00\x00")
    with pytest.raises(AudioProcessingError, match="mono 16-bit PCM"):
        decode_wav_pcm16(buffer.getvalue())


def test_decode_rejects_undecodable_bytes() -> None:
    with pytest.raises(AudioProcessingError, match="could not decode"):
        decode_wav_pcm16(b"definitely not a wav file")


# --- silence ------------------------------------------------------------------


def test_silence_sample_count_is_exact_for_default_pause() -> None:
    # 300ms at 8000Hz is exactly 2400 samples — no rounding.
    assert silence_sample_count(DEFAULT_PAUSE_MS, _SAMPLE_RATE) == 2400


def test_silence_sample_count_rounds_to_nearest() -> None:
    # 1ms at 44100Hz is 44.1 samples -> rounds to 44.
    assert silence_sample_count(1, 44100) == 44


def test_make_silence_is_all_zeros_of_the_exact_length() -> None:
    silence = make_silence(100, _SAMPLE_RATE)
    assert len(silence) == 800
    assert all(sample == 0.0 for sample in silence)


def test_make_silence_zero_duration_is_empty() -> None:
    assert make_silence(0, _SAMPLE_RATE) == []


# --- splice_with_pauses -------------------------------------------------------


def test_splice_inserts_real_silence_between_clips_only() -> None:
    clip_a = ([0.5] * 32, _SAMPLE_RATE)
    clip_b = ([0.25] * 16, _SAMPLE_RATE)
    samples, rate = splice_with_pauses([clip_a, clip_b], pause_ms=100)

    gap = silence_sample_count(100, _SAMPLE_RATE)  # 800
    assert rate == _SAMPLE_RATE
    assert len(samples) == 32 + gap + 16
    # No gap before the first clip or after the last one; real zeros between.
    assert samples[0] == 0.5
    assert samples[-1] == 0.25
    assert all(samples[i] == 0.0 for i in range(32, 32 + gap))
    # The speech samples survive at their real amplitude (the ADR 0064 trap:
    # a clipping bug zeroes nothing but corrupts every non-zero sample).
    assert samples[31] == 0.5
    assert samples[32 + gap] == 0.25


def test_splice_with_zero_pause_concatenates_exactly() -> None:
    clip_a = ([0.5] * 8, _SAMPLE_RATE)
    clip_b = ([0.25] * 8, _SAMPLE_RATE)
    samples, _ = splice_with_pauses([clip_a, clip_b], pause_ms=0)
    assert samples == [0.5] * 8 + [0.25] * 8


def test_splice_single_clip_adds_no_gap() -> None:
    samples, rate = splice_with_pauses([([0.5] * 8, _SAMPLE_RATE)], pause_ms=300)
    assert samples == [0.5] * 8
    assert rate == _SAMPLE_RATE


def test_splice_empty_clip_list_raises() -> None:
    with pytest.raises(AudioProcessingError, match="no decoded clips"):
        splice_with_pauses([], pause_ms=300)


def test_splice_sample_rate_mismatch_raises_clear_error() -> None:
    with pytest.raises(AudioProcessingError, match="sample rates disagree"):
        splice_with_pauses([([0.5], 8000), ([0.5], 16000)], pause_ms=300)


# --- read_wav_clip --------------------------------------------------------------


def test_read_wav_clip_resolves_file_uri_and_decodes(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    path.write_bytes(encode_wav_pcm16([0.5] * 8, _SAMPLE_RATE))
    samples, rate = read_wav_clip(path.as_uri())
    assert rate == _SAMPLE_RATE
    assert len(samples) == 8
    assert samples[0] == pytest.approx(0.5, abs=1 / 32767)


def test_read_wav_clip_unresolvable_scheme_normalized(tmp_path: Path) -> None:
    with pytest.raises(AudioProcessingError):
        read_wav_clip("fake://not-a-real-file.wav")
