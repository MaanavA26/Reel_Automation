"""Provider-neutral contract for the YouTube transcript fabric.

A `TranscriptProvider` retrieves the caption track for a YouTube video. Per
CLAUDE.md §4 this is deterministic IO (a tool/service — "transcript extraction"
is listed there explicitly), kept separate from the pure normalize/chunk steps
so those are testable on fixture segments with no network. Mirrors
`FetchProvider` in `app.services.ingestion.base`.

Timestamps (`start`/`duration` per segment) are intentionally **discarded** in
v1: `Chunk` has no field to carry them and `research_state.py` is out of scope.
This is symmetric with ADR 0008's deferral of `Chunk.parsed_via` — see ADR 0015.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class TranscriptError(RuntimeError):
    """Raised when a YouTube transcript cannot be retrieved or parsed.

    Wraps every provider-side failure mode (transcripts disabled, no transcript
    found, video unavailable, age-restricted, IP/request-blocked, bad URL) into
    one type so `IngestionService` can skip the source uniformly — symmetric
    with `FetchError`/`ParseError`.
    """


class TranscriptSegment(BaseModel):
    """One caption segment — a transient DTO, not a persisted Chunk.

    ``start``/``duration`` (seconds) are captured for provenance fidelity but
    are not yet consumed downstream (no `Chunk` field); see module docstring.
    """

    text: str
    start: float | None = None
    duration: float | None = None


@runtime_checkable
class TranscriptProvider(Protocol):
    """A backend that fetches the caption segments for a YouTube URL.

    Async to match the `FetchProvider` contract (ADR 0002/0008) — fetching is
    network I/O. Implementations raise `TranscriptError` on any failure.
    """

    name: str

    async def fetch(self, *, url: str) -> list[TranscriptSegment]: ...


# Matches the 11-char YouTube video id in the common URL shapes:
# watch?v=ID, youtu.be/ID, /embed/ID, /shorts/ID, /live/ID, or a bare id.
_VIDEO_ID = re.compile(
    r"""(?:
        v=|/v/|youtu\.be/|/embed/|/shorts/|/live/
    )([0-9A-Za-z_-]{11})
    | ^([0-9A-Za-z_-]{11})$
    """,
    re.VERBOSE,
)


def extract_video_id(url: str) -> str:
    """Extract the 11-character YouTube video id from a URL (or a bare id).

    Pure and deterministic — no network. Raises `TranscriptError` if no id can
    be found, so the service skips the source rather than calling the provider
    with garbage.
    """
    match = _VIDEO_ID.search(url.strip())
    if not match:
        raise TranscriptError(f"could not extract a YouTube video id from {url!r}")
    return match.group(1) or match.group(2)


def normalize_transcript(segments: list[TranscriptSegment]) -> str:
    """Join caption segments into a single normalized transcript string.

    Pure and deterministic: strips each segment, drops empties, collapses inner
    whitespace, and space-joins. Timestamps are discarded (see module docstring).
    The result is fed straight into the existing `chunk_text`.
    """
    parts = [re.sub(r"\s+", " ", seg.text.strip()) for seg in segments]
    return " ".join(part for part in parts if part)
