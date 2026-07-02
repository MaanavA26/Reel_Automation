"""Provider-neutral contract for word-level forced alignment (ADR 0062).

A `WordAligner` times each spoken word of already-synthesized narration audio
against the caption segments, returning per-segment `WordSpan` lists — the
timing source word-level karaoke captions need (`format_ass`'s ``\\kf``
emission). Per CLAUDE.md §4 this is deterministic *tool/service* work, never an
agent: the aligner measures where each word lands in the audio; it decides
nothing. Async to match the repo's I/O-bound provider contract (ADR 0002/0003)
— real alignment shells out to an external tool (subprocess I/O).

This module ships the protocol + the hermetic `FakeWordAligner`. The concrete
adapter (`aeneas.py` — DTW/MFCC over eSpeak, CPU-light, the hardware-safe
default per issue #136) treats its tool as an external subprocess contract like
ffmpeg, and stays **documented-not-yet-live** until a real machine runs it (the
ADR 0053 posture). A higher-accuracy neural adapter (WhisperX) is a tracked
follow-up behind this same seam — opt-in only, never the laptop default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.media.schemas import WordSpan


class AlignmentError(RuntimeError):
    """Raised when word-level alignment cannot be performed or fails.

    The seam's single normalized failure type: a missing interpreter/tool, a
    non-zero exit, an unwritable or malformed sync map, and a word-count
    contract violation all surface as this one type — symmetric with
    `CompositionError` / `QCProbeError` — so the pipeline's degrade path
    handles alignment failure uniformly.
    """


def split_words(segment: str) -> list[str]:
    """Tokenize a caption segment into karaoke word units. Pure.

    Whitespace tokenization (`str.split`): punctuation stays attached to its
    word (``"Hello,"`` is one token). Deliberately the single shared rule —
    the unit the aeneas adapter feeds one-per-line, the unit a fake stamps
    synthetic timings onto, and the unit `format_ass` sweeps — so the word
    carriers an aligner returns always re-join to the cue text.
    """
    return segment.split()


@runtime_checkable
class WordAligner(Protocol):
    """A backend that times each spoken word of synthesized narration.

    ``audio_path`` is where the narration audio lives — a local filesystem
    path or a ``file://`` URI (the pipeline passes
    `SynthesizedSpeech.audio_uri` through; resolving it is the adapter's
    concern, and fakes may ignore it). ``segments`` are the caption cue texts
    in cue order. Returns one `WordSpan` list per segment (parallel to
    ``segments``; a segment's spans cover its `split_words` tokens in order),
    with times in integer milliseconds on the narration clock. Failures
    normalize to `AlignmentError`.
    """

    name: str

    async def align(
        self,
        *,
        audio_path: str,
        segments: Sequence[str],
    ) -> list[list[WordSpan]]: ...


@dataclass
class RecordedAlignment:
    """A single `align` invocation captured by the fake."""

    audio_path: str
    segments: list[str]


class FakeWordAligner:
    """A hermetic `WordAligner` for offline tests (no binary, no audio, no I/O).

    Returns deterministic synthetic word timings — a fixed ``ms_per_word``
    cadence on one running clock across all segments (a stand-in for real
    speech timing, the `FakeTTSProvider.ms_per_char` idea at word granularity)
    — and records each call for assertions. Mirrors `FakeCompositionService`.
    """

    name = "fake"

    def __init__(self, *, ms_per_word: int = 300) -> None:
        # A crude, deterministic stand-in for word pacing so timings are
        # text-dependent without a real aligner. Not a claim about accuracy.
        self._ms_per_word = ms_per_word
        self.calls: list[RecordedAlignment] = []

    async def align(
        self,
        *,
        audio_path: str,
        segments: Sequence[str],
    ) -> list[list[WordSpan]]:
        self.calls.append(RecordedAlignment(audio_path=audio_path, segments=list(segments)))
        result: list[list[WordSpan]] = []
        clock_ms = 0
        for segment in segments:
            spans: list[WordSpan] = []
            for word in split_words(segment):
                spans.append(
                    WordSpan(text=word, start_ms=clock_ms, end_ms=clock_ms + self._ms_per_word)
                )
                clock_ms += self._ms_per_word
            result.append(spans)
        return result
