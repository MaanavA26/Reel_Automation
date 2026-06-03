"""Provider-neutral contract for the search fabric.

A `SearchProvider` turns a query into candidate `SearchResult`s. Per CLAUDE.md
§4, this is deterministic *tool/service* work (search, API-wrapping); the search
agent decides *what* to search for, the provider *executes* the search. The
provider — never an LLM — is the only thing that mints a real URL, which keeps
the evidence-vs-inference boundary intact (CLAUDE.md §11; ADR 0006).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.schemas.research_state import SourceType


class SearchResult(BaseModel):
    """A single retrieval hit — a lightweight DTO, not a persisted `Source`.

    Carries only provider-authored fields (no id/timestamp/provenance — those
    are minted when the discovery layer promotes a result to a `Source`).
    """

    url: str
    source_type: SourceType
    title: str | None = None
    snippet: str | None = None


@runtime_checkable
class SearchProvider(Protocol):
    """A search backend that returns candidate results for a query.

    Async to match the workflow node contract (ADR 0002) — real search is
    network I/O. Implementations wrap a search API or the web; the concrete
    adapter is deferred to M-LP (network-gated).
    """

    name: str

    async def search(self, *, query: str, limit: int = 10) -> list[SearchResult]: ...
