"""Subtitle generation: a deterministic seam plus pure SRT/VTT formatters.

Unlike the TTS and composition seams (which wrap a *future* vendor/binary and
therefore ship only a protocol + fake), subtitle generation needs no external
service for its core path — turning text segments + timings into caption files
is pure, deterministic computation. So this module ships **real** code now: the
`SubtitleService` protocol, a concrete `DeterministicSubtitleService`, and pure
stdlib SRT/VTT *formatters* that are fully unit-testable (CLAUDE.md §4 lists
"subtitle generation" as tool work).

The protocol is **synchronous**: the deterministic path is CPU-only, not I/O.
An async variant (e.g. forced alignment against synthesized audio to refine
timings) is deferred to a future milestone — see ADR 0019.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.media.schemas import Caption, CaptionTrack


def _format_timestamp(total_ms: int, *, sep: str) -> str:
    """Render integer milliseconds as ``HH:MM:SS<sep>mmm``.

    `sep` is ``","`` for SRT and ``"."`` for VTT — the one byte that
    distinguishes the two formats' timestamps. Hours are not capped (they roll
    over past 24h); H/M/S are zero-padded to 2 digits and ms to 3.
    """
    if total_ms < 0:
        raise ValueError(f"timestamp must be non-negative, got {total_ms}")
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{ms:03d}"


def _validate_cues(cues: Sequence[Caption]) -> None:
    for i, cue in enumerate(cues):
        if cue.end_ms < cue.start_ms:
            raise ValueError(f"cue {i} has end_ms ({cue.end_ms}) < start_ms ({cue.start_ms})")


def format_srt(track: CaptionTrack) -> str:
    """Render a `CaptionTrack` as SubRip (`.srt`) text.

    SRT cue: a 1-based index line, a ``start --> end`` line using **comma**
    millisecond separators, the text, and a blank-line separator. Pure and
    deterministic.
    """
    _validate_cues(track.cues)
    blocks: list[str] = []
    for i, cue in enumerate(track.cues, start=1):
        start = _format_timestamp(cue.start_ms, sep=",")
        end = _format_timestamp(cue.end_ms, sep=",")
        blocks.append(f"{i}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def format_vtt(track: CaptionTrack) -> str:
    """Render a `CaptionTrack` as WebVTT (`.vtt`) text.

    WebVTT begins with a ``WEBVTT`` header line, then cues whose ``start --> end``
    lines use **period** millisecond separators (no index line is required).
    Pure and deterministic.
    """
    _validate_cues(track.cues)
    blocks: list[str] = ["WEBVTT\n"]
    for cue in track.cues:
        start = _format_timestamp(cue.start_ms, sep=".")
        end = _format_timestamp(cue.end_ms, sep=".")
        blocks.append(f"{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


@runtime_checkable
class SubtitleService(Protocol):
    """Builds a `CaptionTrack` from narration segments and their timings.

    Synchronous: the deterministic path is CPU-only. `segments` and `timings`
    are parallel sequences (segment ``i`` spans ``timings[i]``).
    """

    name: str

    def build_track(
        self,
        *,
        segments: Sequence[str],
        timings: Sequence[tuple[int, int]],
    ) -> CaptionTrack: ...


class DeterministicSubtitleService:
    """A concrete, real `SubtitleService` (no external dependency).

    Zips narration `segments` with their `(start_ms, end_ms)` `timings` into a
    `CaptionTrack`. This is the layer's one shipping-real implementation — the
    seam is not hollow. Pair it with `format_srt` / `format_vtt` to emit caption
    files.
    """

    name = "deterministic"

    def build_track(
        self,
        *,
        segments: Sequence[str],
        timings: Sequence[tuple[int, int]],
    ) -> CaptionTrack:
        if len(segments) != len(timings):
            raise ValueError(
                f"segments ({len(segments)}) and timings ({len(timings)}) must be the same length"
            )
        cues = [
            Caption(start_ms=start, end_ms=end, text=text)
            for text, (start, end) in zip(segments, timings, strict=True)
        ]
        _validate_cues(cues)
        return CaptionTrack(cues=cues, produced_via=f"subtitles:{self.name}")
