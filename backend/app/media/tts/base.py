"""Provider-neutral contract for text-to-speech synthesis.

A `TTSProvider` turns narration text into a `SynthesizedSpeech` audio artifact.
Per CLAUDE.md §4 this is deterministic *tool/service* work (no judgment): the
upstream content strategist decides *what* to say; the provider *executes* the
synthesis. Async to match the repo's I/O-bound provider contract (ADR 0002/0003)
— real TTS is a network call to a vendor (ElevenLabs, Azure, etc.).

This module ships the protocol + a hermetic `FakeTTSProvider`. Concrete adapters
are deferred behind the protocol (the twice-blessed fabric pattern of ADR
0003/0006) to a network-gated milestone — see ADR 0019.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.media.schemas import SynthesizedSpeech


@runtime_checkable
class TTSProvider(Protocol):
    """A TTS backend that synthesizes speech audio from text.

    Implementations wrap a vendor SDK/API and return a `SynthesizedSpeech`
    descriptor (the audio bytes are written to storage out of band; the layer
    traffics in descriptors). The concrete adapter is deferred (ADR 0019).
    """

    name: str

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech: ...


@dataclass
class RecordedSynthesis:
    """A single `synthesize` invocation captured by the fake."""

    text: str
    voice: str


class FakeTTSProvider:
    """A hermetic `TTSProvider` for offline tests (no network, no audio).

    Returns a deterministic `SynthesizedSpeech` descriptor with a synthetic
    `audio_uri` and a duration derived from the text length (a stand-in for real
    speech timing), and records each call for assertions. Mirrors
    `app.services.search.fakes.FakeSearchProvider`.
    """

    name = "fake"

    def __init__(self, *, ms_per_char: int = 60) -> None:
        # A crude, deterministic stand-in for speech pacing so the duration is
        # text-dependent (useful for downstream timing tests) without a real
        # synthesizer. Not a claim about real TTS speed.
        self._ms_per_char = ms_per_char
        self.calls: list[RecordedSynthesis] = []

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        self.calls.append(RecordedSynthesis(text=text, voice=voice))
        return SynthesizedSpeech(
            audio_uri=f"fake://tts/{voice}/{len(self.calls)}.wav",
            duration_ms=len(text) * self._ms_per_char,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )
