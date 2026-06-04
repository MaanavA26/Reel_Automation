"""Source ingestion — deterministic fetch + parse of discovered sources → Chunks.

Per CLAUDE.md §4 this band is *tool/service* work (fetch, parse, normalize — no
judgment, no LLM). It mirrors the search fabric: a `FetchProvider` protocol
(network boundary) with a hermetic `FakeFetchProvider` and a real
`HttpxFetchProvider`, plus pure `parse_html` / `chunk_text` functions and an
`IngestionService` that orchestrates them. See ADR 0008.
"""

from __future__ import annotations

from app.services.ingestion.base import FetchedContent, FetchError, FetchProvider
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.httpx_fetch import HttpxFetchProvider
from app.services.ingestion.parser import parse_html
from app.services.ingestion.service import IngestionError, IngestionService

__all__ = [
    "FakeFetchProvider",
    "FetchError",
    "FetchProvider",
    "FetchedContent",
    "HttpxFetchProvider",
    "IngestionError",
    "IngestionService",
    "chunk_text",
    "parse_html",
]
