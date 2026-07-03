"""Tests for `SegmentedTTSProvider` — uniform inter-sentence pause splicing.

Fully offline/hermetic: a local `_WavFakeTTSProvider` produces *real* mono
16-bit PCM WAV clips (unlike the shared `FakeTTSProvider`, which returns a bare
`fake://` descriptor with no audio bytes — insufficient here because
`SegmentedTTSProvider` must decode each per-sentence clip back to PCM). Clips
are persisted to `tmp_path` via a real `file://` sink so `resolve_local_path`
resolves them exactly as it would a real Kokoro clip.

Sample counts are chosen as exact millisecond multiples (8 samples/char at an
8000 Hz rate == 1 ms/char) and `DEFAULT_PAUSE_MS`-friendly (300ms * 8000Hz /
1000 == 2400 samples, no rounding) so duration arithmetic is exact and the
gap boundaries land on precisely known sample indices.
"""

from __future__ import annotations

import array
import asyncio
import io
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.kokoro import KokoroTtsError, duration_ms_from_samples, encode_wav_pcm16
from app.media.tts.segmented import (
    DEFAULT_PAUSE_MS,
    SegmentedTtsError,
    SegmentedTTSProvider,
    split_into_sentences,
)

_SAMPLE_RATE = 8000
_SAMPLES_PER_CHAR = 8  # at 8000Hz, 8 samples == 1ms -> duration == len(text) ms exactly
_AMPLITUDE = 0.5


@dataclass
class _RecordedCall:
    text: str
    voice: str


class _FileSink:
    """A real filesystem `AudioSink`: writes bytes under `tmp_path`, returns file://."""

    def __init__(self, directory: Path, prefix: str) -> None:
        self._directory = directory
        self._prefix = prefix
        self.written: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.written.append(audio)
        path = self._directory / f"{self._prefix}-{len(self.written)}.wav"
        path.write_bytes(audio)
        return path.as_uri()


class _WavFakeTTSProvider:
    """A `TTSProvider` stub that returns real, decodable WAV clips.

    Each call encodes ``len(text) * samples_per_char`` constant-``amplitude``
    samples at ``sample_rate`` via the real `encode_wav_pcm16` (so the produced
    bytes are exactly what a real adapter would hand `SegmentedTTSProvider`),
    persists them through the injected sink, and records the call. Optional
    knobs let tests force a sample-rate mismatch or a mid-sequence failure.
    """

    name = "wavfake"

    def __init__(
        self,
        sink: _FileSink,
        *,
        sample_rate: int = _SAMPLE_RATE,
        samples_per_char: int = _SAMPLES_PER_CHAR,
        amplitude: float = _AMPLITUDE,
        rate_by_call: list[int] | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self._sink = sink
        self._sample_rate = sample_rate
        self._samples_per_char = samples_per_char
        self._amplitude = amplitude
        self._rate_by_call = rate_by_call
        self._fail_on_call = fail_on_call
        self.calls: list[_RecordedCall] = []

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        self.calls.append(_RecordedCall(text=text, voice=voice))
        call_index = len(self.calls) - 1
        if self._fail_on_call is not None and call_index == self._fail_on_call:
            raise KokoroTtsError(f"synthetic failure on call {call_index}")

        rate = self._rate_by_call[call_index] if self._rate_by_call else self._sample_rate
        sample_count = len(text) * self._samples_per_char
        samples = [self._amplitude] * sample_count
        audio_bytes = encode_wav_pcm16(samples, rate)
        duration_ms = duration_ms_from_samples(sample_count, rate)
        audio_uri = self._sink(audio_bytes)
        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )


def _decode(audio_uri: str) -> tuple[list[float], int]:
    """Test-side WAV decode helper — independent of the module under test."""
    path = Path(audio_uri.removeprefix("file://"))
    with wave.open(io.BytesIO(path.read_bytes()), "rb") as wav_in:
        sample_rate = wav_in.getframerate()
        raw = wav_in.readframes(wav_in.getnframes())
    pcm = array.array("h")
    pcm.frombytes(raw)
    return [v / 32767.0 for v in pcm], sample_rate


def _synthesize(
    provider: SegmentedTTSProvider, *, text: str, voice: str = "narrator"
) -> SynthesizedSpeech:
    return asyncio.run(provider.synthesize(text=text, voice=voice))


# --- split_into_sentences (pure) --------------------------------------------


def test_split_basic_terminal_punctuation() -> None:
    assert split_into_sentences("Hello world. How are you? I am fine!") == [
        "Hello world.",
        "How are you?",
        "I am fine!",
    ]


def test_split_collapses_multiple_spaces_between_sentences() -> None:
    assert split_into_sentences("One.   Two.") == ["One.", "Two."]


def test_split_filters_empty_piece_from_trailing_punctuation_and_whitespace() -> None:
    # Trailing whitespace after the final '.' makes re.split emit a trailing
    # empty piece — it must be filtered, mirroring _split_into_beats.
    assert split_into_sentences("Only one sentence.  ") == ["Only one sentence."]


def test_split_whitespace_only_input_yields_no_sentences() -> None:
    assert split_into_sentences("   ") == []
    assert split_into_sentences("") == []


def test_split_known_limitation_abbreviation_false_split() -> None:
    # Documented limitation (module docstring, Decision 1): "Dr." looks like a
    # sentence end to the regex. This test locks the *current* (imperfect)
    # behavior so a future fix is a deliberate, visible change — not solved here.
    assert split_into_sentences("Dr. Smith arrived. He was late.") == [
        "Dr.",
        "Smith arrived.",
        "He was late.",
    ]


# --- SegmentedTTSProvider.synthesize ----------------------------------------


def test_satisfies_protocol(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))
    assert isinstance(provider, TTSProvider)


def test_multi_sentence_calls_inner_once_per_sentence_with_exact_texts(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    _synthesize(provider, text="One. Two. Three.")

    assert [c.text for c in inner.calls] == ["One.", "Two.", "Three."]
    assert all(c.voice == "narrator" for c in inner.calls)


def test_multi_sentence_duration_equals_sum_plus_gaps(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    speech = _synthesize(provider, text="One. Two. Three.")

    # "One."=4 chars=4ms, "Two."=4ms, "Three."=6ms; 2 gaps at DEFAULT_PAUSE_MS.
    expected = 4 + 4 + 6 + 2 * DEFAULT_PAUSE_MS
    assert speech.duration_ms == expected


def test_multi_sentence_splices_real_silence_at_exact_gap_positions(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    speech = _synthesize(provider, text="One. Two. Three.")
    samples, sample_rate = _decode(speech.audio_uri)

    assert sample_rate == _SAMPLE_RATE
    gap_samples = DEFAULT_PAUSE_MS * _SAMPLE_RATE // 1000
    seg1_len = len("One.") * _SAMPLES_PER_CHAR  # 32
    seg2_len = len("Two.") * _SAMPLES_PER_CHAR  # 32
    seg3_len = len("Three.") * _SAMPLES_PER_CHAR  # 48

    gap1_start = seg1_len
    gap1_end = gap1_start + gap_samples
    seg2_start = gap1_end
    gap2_start = seg2_start + seg2_len
    gap2_end = gap2_start + gap_samples
    seg3_start = gap2_end

    assert len(samples) == seg1_len + gap_samples + seg2_len + gap_samples + seg3_len

    # The gaps really are silence (zero samples) at every position, not just
    # "the duration adds up" — this is the discriminating assertion against a
    # clipping bug that would corrupt speech samples but leave true zeros alone.
    for i in range(gap1_start, gap1_end):
        assert samples[i] == 0.0
    for i in range(gap2_start, gap2_end):
        assert samples[i] == 0.0

    # And the *speech* samples survived the decode/splice/re-encode round trip
    # at their real amplitude — not clamped to full scale (the trap: feeding
    # raw int16 values straight into encode_wav_pcm16 would clamp every
    # non-zero speech sample to +-1.0, while zeros would stay zero and the
    # duration math would still check out).
    assert samples[0] == pytest.approx(_AMPLITUDE, abs=1e-3)
    assert samples[seg1_len - 1] == pytest.approx(_AMPLITUDE, abs=1e-3)
    assert samples[seg2_start] == pytest.approx(_AMPLITUDE, abs=1e-3)
    assert samples[seg3_start] == pytest.approx(_AMPLITUDE, abs=1e-3)
    assert samples[-1] == pytest.approx(_AMPLITUDE, abs=1e-3)


def test_multi_sentence_produced_via_names_both_wrapper_and_inner(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    speech = _synthesize(provider, text="One. Two.")

    assert speech.produced_via == "tts:segmented+wavfake"
    assert speech.voice == "narrator"


def test_multi_sentence_persists_via_the_providers_own_sink_not_inners(tmp_path: Path) -> None:
    inner_sink = _FileSink(tmp_path, "inner")
    final_sink = _FileSink(tmp_path, "final")
    inner = _WavFakeTTSProvider(inner_sink)
    provider = SegmentedTTSProvider(inner, final_sink)

    speech = _synthesize(provider, text="One. Two.")

    assert len(inner_sink.written) == 2  # one per-sentence scratch clip
    assert len(final_sink.written) == 1  # one final spliced clip
    assert "final-1.wav" in speech.audio_uri


# --- degenerate: single sentence --------------------------------------------


def test_single_sentence_calls_inner_exactly_once(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    _synthesize(provider, text="Hello world.")

    assert len(inner.calls) == 1
    assert inner.calls[0].text == "Hello world."


def test_single_sentence_returns_inner_result_verbatim_no_pause_no_reencode(
    tmp_path: Path,
) -> None:
    inner_sink = _FileSink(tmp_path, "inner")
    final_sink = _FileSink(tmp_path, "final")
    inner = _WavFakeTTSProvider(inner_sink)
    provider = SegmentedTTSProvider(inner, final_sink)

    speech = _synthesize(provider, text="Hello world.")

    # Byte-for-byte the same result the wrapped provider would give directly:
    # same URI, same produced_via, same duration — and the wrapper's own sink
    # was never touched (no decode/re-encode round trip took place).
    assert speech.audio_uri == "file://" + str(tmp_path / "inner-1.wav")
    assert speech.produced_via == "tts:wavfake"
    assert speech.duration_ms == duration_ms_from_samples(
        len("Hello world.") * _SAMPLES_PER_CHAR, _SAMPLE_RATE
    )
    assert final_sink.written == []


# --- failure / error handling -----------------------------------------------


def test_sample_rate_mismatch_raises_clear_error(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"), rate_by_call=[8000, 16000])
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    with pytest.raises(SegmentedTtsError, match="sample rates disagree"):
        _synthesize(provider, text="One. Two.")


def test_per_sentence_failure_propagates_unwrapped(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"), fail_on_call=1)
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    # The wrapped provider's own error type surfaces unchanged — never
    # silently dropped, never rewrapped in an unrelated type.
    with pytest.raises(KokoroTtsError, match="synthetic failure on call 1"):
        _synthesize(provider, text="One. Two. Three.")


def test_whitespace_only_text_raises(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"))

    with pytest.raises(SegmentedTtsError, match="no narratable sentences"):
        _synthesize(provider, text="   ")
    assert inner.calls == []


def test_negative_pause_ms_rejected_at_construction(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    with pytest.raises(SegmentedTtsError, match="pause_ms must be non-negative"):
        SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"), pause_ms=-1)


def test_non_wav_or_wrong_shape_clip_raises_clear_error(tmp_path: Path) -> None:
    class _StereoFakeTTSProvider:
        name = "stereo"

        async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_out:
                wav_out.setnchannels(2)  # unsupported: SegmentedTTSProvider requires mono
                wav_out.setsampwidth(2)
                wav_out.setframerate(_SAMPLE_RATE)
                wav_out.writeframes(b"\x00\x00\x00\x00")
            path = tmp_path / f"stereo-{voice}.wav"
            path.write_bytes(buffer.getvalue())
            return SynthesizedSpeech(
                audio_uri=path.as_uri(),
                duration_ms=1,
                voice=voice,
                produced_via="tts:stereo",
            )

    provider = SegmentedTTSProvider(_StereoFakeTTSProvider(), _FileSink(tmp_path, "final"))

    with pytest.raises(SegmentedTtsError, match="mono 16-bit PCM"):
        _synthesize(provider, text="One. Two.")


def test_unresolvable_uri_scheme_normalized_to_segmented_error(tmp_path: Path) -> None:
    class _FakeSchemeProvider:
        name = "fakescheme"

        async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
            return SynthesizedSpeech(
                audio_uri="fake://not-a-real-file.wav",
                duration_ms=1,
                voice=voice,
                produced_via="tts:fakescheme",
            )

    provider = SegmentedTTSProvider(_FakeSchemeProvider(), _FileSink(tmp_path, "final"))

    with pytest.raises(SegmentedTtsError):
        _synthesize(provider, text="One. Two.")


# --- pause_ms configurability ------------------------------------------------


def test_default_pause_ms_constant() -> None:
    assert DEFAULT_PAUSE_MS == 300


def test_custom_pause_ms_changes_gap_length(tmp_path: Path) -> None:
    inner = _WavFakeTTSProvider(_FileSink(tmp_path, "inner"))
    provider = SegmentedTTSProvider(inner, _FileSink(tmp_path, "final"), pause_ms=100)

    speech = _synthesize(provider, text="One. Two.")

    expected_gap_samples = 100 * _SAMPLE_RATE // 1000
    seg_len = len("One.") * _SAMPLES_PER_CHAR
    expected_total = seg_len * 2 + expected_gap_samples
    assert speech.duration_ms == duration_ms_from_samples(expected_total, _SAMPLE_RATE)
