"""In-memory `TranscriptProvider` for hermetic tests (no network).

Replays scripted segments per URL and records calls. Mirrors
`FakeFetchProvider` in `app.services.ingestion.fakes`; kept in its own module so
the sibling-edited `fakes.py` stays untouched (see ADR 0015).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.ingestion.transcript import TranscriptError, TranscriptSegment


@dataclass
class RecordedTranscriptFetch:
    """A single ``fetch`` invocation captured by the fake."""

    url: str


class FakeTranscriptProvider:
    """A `TranscriptProvider` that returns scripted segments keyed by URL.

    Construct with a mapping of url → ``list[TranscriptSegment]``. An unmapped
    URL raises `TranscriptError`, so tests can exercise the per-source skip path
    (no transcript / disabled / age-restricted all surface as `TranscriptError`).
    """

    name = "fake"

    def __init__(self, by_url: Mapping[str, list[TranscriptSegment]] | None = None) -> None:
        self._by_url: dict[str, list[TranscriptSegment]] = dict(by_url or {})
        self.calls: list[RecordedTranscriptFetch] = []

    async def fetch(self, *, url: str) -> list[TranscriptSegment]:
        self.calls.append(RecordedTranscriptFetch(url=url))
        if url not in self._by_url:
            raise TranscriptError(f"no scripted transcript for {url!r}")
        return self._by_url[url]
