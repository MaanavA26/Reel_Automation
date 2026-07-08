"""Shared hermetic WAV-emitting test doubles for splice-consuming seams.

`NarrationSynthesizer` (and `SegmentedTTSProvider` before it) must *decode*
each per-clip WAV back to PCM, so the shared `FakeTTSProvider` (which returns a
bare ``fake://`` descriptor with no audio bytes) is insufficient — these stubs
produce **real** mono 16-bit PCM WAV clips via the real `encode_wav_pcm16` and
persist them to a real ``file://`` sink, exactly the shape a real Kokoro clip
has. They mirror `tests/media/tts/test_segmented.py`'s module-local stubs
(kept local there so that suite stays provably unmodified by ADR 0067's
refactor); new suites import from here instead of copying them again.

The default numbers are chosen for exact arithmetic: 8 samples/char at 8000 Hz
is exactly 1 ms/char, and `DEFAULT_PAUSE_MS` (300ms) is exactly 2400 samples —
no rounding anywhere, so tests can assert offsets to the millisecond.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.media.schemas import SynthesizedSpeech
from app.media.tts.kokoro import KokoroTtsError, duration_ms_from_samples, encode_wav_pcm16

SAMPLE_RATE = 8000
SAMPLES_PER_CHAR = 8  # at 8000Hz, 8 samples == 1ms -> duration == len(text) ms exactly
AMPLITUDE = 0.5


@dataclass
class RecordedCall:
    """A single `synthesize` invocation captured by `WavFakeTTSProvider`."""

    text: str
    voice: str


class FileSink:
    """A real filesystem `AudioSink`: writes bytes under a dir, returns file://."""

    def __init__(self, directory: Path, prefix: str) -> None:
        self._directory = directory
        self._prefix = prefix
        self.written: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.written.append(audio)
        path = self._directory / f"{self._prefix}-{len(self.written)}.wav"
        path.write_bytes(audio)
        return path.as_uri()


class WavFakeTTSProvider:
    """A `TTSProvider` stub that returns real, decodable WAV clips.

    Each call encodes ``len(text) * samples_per_char`` constant-``amplitude``
    samples at ``sample_rate`` via the real `encode_wav_pcm16` (so the produced
    bytes are exactly what a real adapter would hand a splice-consuming seam),
    persists them through the injected sink, and records the call. Optional
    knobs let tests force a sample-rate mismatch or a mid-sequence failure.
    """

    name = "wavfake"

    def __init__(
        self,
        sink: FileSink,
        *,
        sample_rate: int = SAMPLE_RATE,
        samples_per_char: int = SAMPLES_PER_CHAR,
        amplitude: float = AMPLITUDE,
        rate_by_call: list[int] | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self._sink = sink
        self._sample_rate = sample_rate
        self._samples_per_char = samples_per_char
        self._amplitude = amplitude
        self._rate_by_call = rate_by_call
        self._fail_on_call = fail_on_call
        self.calls: list[RecordedCall] = []

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        self.calls.append(RecordedCall(text=text, voice=voice))
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
