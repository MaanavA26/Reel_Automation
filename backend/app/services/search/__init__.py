"""Search fabric — provider-neutral source retrieval (services layer).

Mirrors the LLM fabric (`app.services.llm`): a `SearchProvider` protocol that
turns a query into candidate `SearchResult`s, plus a hermetic `FakeSearchProvider`
for offline tests. Per CLAUDE.md §4, search/IO is deterministic *tool* work — an
LLM never mints a source. The concrete network adapter (Tavily/Brave/etc.) lands
with its first network-enabled run (M-LP); this package ships the contract and
the fake. See ADR 0006.
"""

from __future__ import annotations

from app.services.search.base import SearchProvider, SearchResult
from app.services.search.fakes import FakeSearchProvider

__all__ = [
    "FakeSearchProvider",
    "SearchProvider",
    "SearchResult",
]
