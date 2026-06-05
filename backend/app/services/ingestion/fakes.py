"""In-memory `FetchProvider` for hermetic tests (no network).

Replays scripted `FetchedContent` per URL and records calls. Mirrors
`app.services.search.fakes.FakeSearchProvider`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.ingestion.base import FetchedContent, FetchError


@dataclass
class RecordedFetch:
    """A single ``fetch`` invocation captured by the fake."""

    url: str


class FakeFetchProvider:
    """A `FetchProvider` that returns scripted content keyed by URL.

    Construct with a mapping of url → `FetchedContent`. An unmapped URL raises
    `FetchError`, so tests can exercise the per-source skip path.
    """

    name = "fake"

    def __init__(self, by_url: Mapping[str, FetchedContent] | None = None) -> None:
        self._by_url: dict[str, FetchedContent] = dict(by_url or {})
        self.calls: list[RecordedFetch] = []

    async def fetch(self, *, url: str) -> FetchedContent:
        self.calls.append(RecordedFetch(url=url))
        if url not in self._by_url:
            raise FetchError(f"no scripted content for {url!r}")
        return self._by_url[url]
