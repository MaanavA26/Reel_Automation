"""Ingestion service — orchestrates fetch + parse + chunk over discovered sources.

A deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM): given the band's
`Source`s, it fetches each (web only in v1), parses to text, and chunks. Per-source
fetch/parse failures are tolerated (skipped + logged); it raises `IngestionError`
only when *no* chunks result at all — mirroring the "never advance on empty
acquisition" contract used by the discovery agent. See ADR 0008.
"""

from __future__ import annotations

import logging

from app.schemas.research_state import Chunk, Source, SourceType
from app.services.ingestion.base import FetchError, FetchProvider
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.parser import ParseError, parse_html

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when ingestion yields no chunks across all sources."""


class IngestionService:
    """Turns `Source`s into `Chunk`s via an injected `FetchProvider`."""

    def __init__(self, fetch_provider: FetchProvider) -> None:
        self._fetch = fetch_provider

    async def ingest(self, sources: list[Source]) -> list[Chunk]:
        """Fetch + parse + chunk each web source; skip the rest and any failures.

        Non-WEB sources are skipped in v1 (PDF/YouTube/repo parsers land later).
        Raises `IngestionError` if no chunk is produced from any source.
        """
        chunks: list[Chunk] = []
        for source in sources:
            if source.type is not SourceType.WEB:
                logger.info("ingestion: skipping non-web source %s (%s)", source.id, source.type)
                continue
            try:
                fetched = await self._fetch.fetch(url=source.url)
                text = parse_html(fetched.content, fetched.content_type)
                chunks.extend(chunk_text(text, source_id=source.id))
            except (FetchError, ParseError) as exc:
                logger.warning("ingestion: skipping source %s: %s", source.id, exc)
                continue

        if not chunks:
            raise IngestionError("ingestion produced no chunks from any source")
        return chunks
