"""Tests for `NarrationSynthesizer` — per-beat synthesis, exact offsets (ADR 0067).

Fully offline/hermetic: the shared `WavFakeTTSProvider` produces real mono
16-bit PCM WAV clips persisted via a real ``file://`` sink (`tests.media.
wav_fakes`), so the decode/splice/offset math under test runs against exactly
the byte shape a real Kokoro clip has. The stub's numbers make arithmetic
exact — 8 samples/char at 8000 Hz is 1 ms/char, and every pause length used
here divides into whole samples — so offsets are asserted to the millisecond,
not approximately.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from pathlib import Path

import pytest

from app.media.audio import DEFAULT_PAUSE_MS
from app.media.narration import BeatNarration, NarrationError, NarrationSynthesizer
from app.media.tts.kokoro import KokoroTtsError
from tests.media.wav_fakes import FileSink, WavFakeTTSProvider

# 1 ms/char (see wav_fakes): beat durations are simply the text lengths.
_SEGMENTS = ["One beat.", "Two.", "Three, longer."]  # 9ms, 4ms, 14ms


def _synthesize(
    synthesizer: NarrationSynthesizer,
    *,
    segments: list[str],
    voice: str = "narrator",
) -> BeatNarration:
    return asyncio.run(synthesizer.synthesize(segments=segments, voice=voice))


def _build(
    tmp_path: Path, **kwargs: int
) -> tuple[NarrationSynthesizer, WavFakeTTSProvider, FileSink]:
    inner = WavFakeTTSProvider(FileSink(tmp_path, "clip"))
    final_sink = FileSink(tmp_path, "final")
    return NarrationSynthesizer(inner, final_sink, **kwargs), inner, final_sink


# --- exact offsets (the load-bearing contract) --------------------------------


def test_offsets_are_exact_contiguous_and_cover_the_total(tmp_path: Path) -> None:
    synthesizer, _, _ = _build(tmp_path, pause_ms=100)
    result = _synthesize(synthesizer, segments=_SEGMENTS)

    # Exact construction-time spans: clip lengths 9/4/14 ms with 100ms gaps,
    # each cue's end extended to the next cue's start (gap ownership) and the
    # last cue ending exactly at the total duration.
    assert result.cue_timings == [(0, 109), (109, 213), (213, 227)]
    assert result.speech.duration_ms == 227
    assert result.cue_timings[0][0] == 0
    assert result.cue_timings[-1][1] == result.speech.duration_ms
    for (_, prev_end), (next_start, _) in pairwise(result.cue_timings):
        assert prev_end == next_start  # touching exactly, never overlapping


def test_default_pause_is_the_shared_constant(tmp_path: Path) -> None:
    synthesizer, _, _ = _build(tmp_path)
    result = _synthesize(synthesizer, segments=_SEGMENTS)

    assert DEFAULT_PAUSE_MS == 300
    assert result.cue_timings == [(0, 309), (309, 613), (613, 627)]
    assert result.speech.duration_ms == 9 + 4 + 14 + 2 * DEFAULT_PAUSE_MS


def test_cue_end_includes_the_trailing_gap(tmp_path: Path) -> None:
    # Gap ownership, asserted explicitly: cue 0's clip is 9ms of speech but
    # its cue span is 109ms — the 100ms inter-beat silence belongs to the
    # earlier cue (ADR 0067 Decision 2), preserving full coverage with no
    # caption-free dead air, exactly like ADR 0065's gap bridging chose.
    synthesizer, _, _ = _build(tmp_path, pause_ms=100)
    result = _synthesize(synthesizer, segments=["One beat.", "Two."])

    clip_speech_ms = len("One beat.")  # 9
    start, end = result.cue_timings[0]
    assert end - start == clip_speech_ms + 100
    assert result.cue_timings == [(0, 109), (109, 113)]


def test_zero_pause_yields_back_to_back_clip_spans(tmp_path: Path) -> None:
    synthesizer, _, _ = _build(tmp_path, pause_ms=0)
    result = _synthesize(synthesizer, segments=_SEGMENTS)

    assert result.cue_timings == [(0, 9), (9, 13), (13, 27)]
    assert result.speech.duration_ms == 27


# --- per-beat synthesis + clip URIs -------------------------------------------


def test_synthesizes_each_segment_as_its_own_clip(tmp_path: Path) -> None:
    synthesizer, inner, _ = _build(tmp_path, pause_ms=100)
    _synthesize(synthesizer, segments=_SEGMENTS, voice="alice")

    assert [c.text for c in inner.calls] == _SEGMENTS
    assert all(c.voice == "alice" for c in inner.calls)


def test_clip_uris_are_parallel_to_segments_and_point_at_real_clips(tmp_path: Path) -> None:
    synthesizer, _, _ = _build(tmp_path, pause_ms=100)
    result = _synthesize(synthesizer, segments=_SEGMENTS)

    assert len(result.clip_uris) == len(_SEGMENTS)
    # The wav fake's sink names clips in call order — the URIs the per-clip
    # aligner will be handed must follow segment order exactly.
    for i, uri in enumerate(result.clip_uris, start=1):
        assert uri.endswith(f"clip-{i}.wav")
        assert Path(uri.removeprefix("file://")).exists()


def test_final_audio_persisted_via_own_sink_with_provenance(tmp_path: Path) -> None:
    synthesizer, _, final_sink = _build(tmp_path, pause_ms=100)
    result = _synthesize(synthesizer, segments=_SEGMENTS, voice="alice")

    assert len(final_sink.written) == 1  # exactly one final spliced WAV
    assert "final-1.wav" in result.speech.audio_uri
    assert result.speech.produced_via == "tts:per-beat+wavfake"
    assert result.speech.voice == "alice"


# --- single-beat fast path -----------------------------------------------------


def test_single_segment_returns_inner_clip_verbatim(tmp_path: Path) -> None:
    synthesizer, inner, final_sink = _build(tmp_path)
    result = _synthesize(synthesizer, segments=["Only beat."])

    assert len(inner.calls) == 1
    assert result.speech.audio_uri.endswith("clip-1.wav")
    assert result.speech.produced_via == "tts:wavfake"  # the inner's own value
    assert result.cue_timings == [(0, result.speech.duration_ms)]
    assert result.clip_uris == [result.speech.audio_uri]
    assert final_sink.written == []  # no decode/re-encode round trip happened


# --- failure handling -----------------------------------------------------------


def test_empty_segment_list_raises_before_any_synthesis(tmp_path: Path) -> None:
    synthesizer, inner, _ = _build(tmp_path)
    with pytest.raises(NarrationError, match="no narration segments"):
        _synthesize(synthesizer, segments=[])
    assert inner.calls == []


def test_blank_segment_fails_loud_never_dropped(tmp_path: Path) -> None:
    synthesizer, inner, _ = _build(tmp_path)
    with pytest.raises(NarrationError, match="segment 1 is blank"):
        _synthesize(synthesizer, segments=["Real beat.", "   ", "Another."])
    assert inner.calls == []  # validated up front, nothing half-synthesized


def test_per_beat_synthesis_failure_propagates_unwrapped(tmp_path: Path) -> None:
    inner = WavFakeTTSProvider(FileSink(tmp_path, "clip"), fail_on_call=1)
    synthesizer = NarrationSynthesizer(inner, FileSink(tmp_path, "final"))
    # The wrapped provider's own error type surfaces unchanged — content is
    # never silently dropped, callers keep handling one error type per backend.
    with pytest.raises(KokoroTtsError, match="synthetic failure on call 1"):
        _synthesize(synthesizer, segments=_SEGMENTS)


def test_missing_clip_file_normalized_to_narration_error(tmp_path: Path) -> None:
    # A per-beat clip whose URI points at a file that no longer exists must
    # surface as NarrationError (via the shared audio contract) — never a raw
    # FileNotFoundError/OSError escaping `synthesize`.
    class _VanishingSink(FileSink):
        def __call__(self, audio: bytes) -> str:
            uri = super().__call__(audio)
            Path(uri.removeprefix("file://")).unlink()  # gone before the read-back
            return uri

    inner = WavFakeTTSProvider(_VanishingSink(tmp_path, "clip"))
    synthesizer = NarrationSynthesizer(inner, FileSink(tmp_path, "final"))
    with pytest.raises(NarrationError, match="could not read audio clip"):
        _synthesize(synthesizer, segments=["One.", "Two."])


def test_garbage_clip_bytes_normalized_to_narration_error(tmp_path: Path) -> None:
    # A per-beat clip whose file holds non-WAV bytes must surface as
    # NarrationError — never a raw wave.Error/EOFError.
    class _CorruptingSink(FileSink):
        def __call__(self, audio: bytes) -> str:
            return super().__call__(b"definitely not a wav file")

    inner = WavFakeTTSProvider(_CorruptingSink(tmp_path, "clip"))
    synthesizer = NarrationSynthesizer(inner, FileSink(tmp_path, "final"))
    with pytest.raises(NarrationError, match="could not decode"):
        _synthesize(synthesizer, segments=["One.", "Two."])


def test_sample_rate_mismatch_normalized_to_narration_error(tmp_path: Path) -> None:
    inner = WavFakeTTSProvider(FileSink(tmp_path, "clip"), rate_by_call=[8000, 16000])
    synthesizer = NarrationSynthesizer(inner, FileSink(tmp_path, "final"))
    with pytest.raises(NarrationError, match="sample rates disagree"):
        _synthesize(synthesizer, segments=["One.", "Two."])


def test_negative_pause_rejected_at_construction(tmp_path: Path) -> None:
    inner = WavFakeTTSProvider(FileSink(tmp_path, "clip"))
    with pytest.raises(NarrationError, match="pause_ms must be non-negative"):
        NarrationSynthesizer(inner, FileSink(tmp_path, "final"), pause_ms=-1)
