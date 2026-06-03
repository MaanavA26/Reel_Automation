"""In-memory `SearchProvider` for hermetic tests (no network).

A factory-style fake (testing-standards: "don't mock what you can fake") that
returns scripted results per query and records the queries it received. Mirrors
`app.services.llm.fakes.FakeProvider`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.search.base import SearchResult


@dataclass
class RecordedSearch:
    """A single `search` invocation captured by the fake."""

    query: str
    limit: int


class FakeSearchProvider:
    """A `SearchProvider` that replays scripted results.

    Construct with either a flat list of results (returned for every query) or a
    per-query mapping. Records each call for assertions. Returns at most ``limit``
    results, mirroring a real backend.
    """

    name = "fake"

    def __init__(
        self,
        results: Sequence[SearchResult] | None = None,
        *,
        by_query: Mapping[str, Sequence[SearchResult]] | None = None,
    ) -> None:
        self._results: list[SearchResult] = list(results or [])
        self._by_query: dict[str, list[SearchResult]] = {
            q: list(rs) for q, rs in (by_query or {}).items()
        }
        self.calls: list[RecordedSearch] = []

    async def search(self, *, query: str, limit: int = 10) -> list[SearchResult]:
        self.calls.append(RecordedSearch(query=query, limit=limit))
        hits = self._by_query.get(query, self._results)
        return list(hits[:limit])
