"""Ingestion service — orchestrates fetch + parse + chunk over discovered sources.

A deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM): given the band's
`Source`s, it fetches each, parses to text by source type (HTML for WEB, the
text layer for PDF), and chunks. Per-source fetch/parse failures are tolerated
(skipped + logged); it raises `IngestionError` only when *no* chunks result at
all — mirroring the "never advance on empty acquisition" contract used by the
discovery agent. WEB landed in M6 (ADR 0008); PDF in M-LP (ADR 0014).
"""

from __future__ import annotations

import logging

from app.schemas.research_state import Chunk, Source, SourceType
from app.services.ingestion.base import FetchError, FetchProvider, PdfParser
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.parser import ParseError, parse_html
from app.services.ingestion.pdf_parser import PypdfParser

logger = logging.getLogger(__name__)

# Source types this service ingests in the current milestone. Others are skipped
# (YouTube/repo/paper parsers land behind the same seam later — ADR 0014).
_SUPPORTED_TYPES = (SourceType.WEB, SourceType.PDF)


class IngestionError(RuntimeError):
    """Raised when ingestion yields no chunks across all sources."""


class IngestionService:
    """Turns `Source`s into `Chunk`s via an injected `FetchProvider` + `PdfParser`."""

    def __init__(
        self,
        fetch_provider: FetchProvider,
        pdf_parser: PdfParser | None = None,
    ) -> None:
        self._fetch = fetch_provider
        # Default to the real ``pypdf`` adapter — its import is lazy (inside
        # ``parse``), so constructing it offline is safe; a missing dependency
        # surfaces as a per-source `ParseError` skip at ingest time, not here.
        self._pdf_parser: PdfParser = pdf_parser or PypdfParser()

    async def ingest(self, sources: list[Source]) -> list[Chunk]:
        """Fetch + parse + chunk each supported source; skip the rest and failures.

        WEB sources are HTML-parsed; PDF sources go through the injected
        `PdfParser` (text layer only — scanned/image PDFs await the deferred OCR
        path). Other source types are skipped in this milestone. Raises
        `IngestionError` if no chunk is produced from any source.
        """
        chunks: list[Chunk] = []
        for source in sources:
            if source.type not in _SUPPORTED_TYPES:
                logger.info(
                    "ingestion: skipping unsupported source %s (%s)", source.id, source.type
                )
                continue
            try:
                fetched = await self._fetch.fetch(url=source.url)
                if source.type is SourceType.PDF:
                    text = self._pdf_parser.parse(fetched.content)
                else:
                    text = parse_html(fetched.content, fetched.content_type)
                chunks.extend(chunk_text(text, source_id=source.id))
            except (FetchError, ParseError) as exc:
                logger.warning("ingestion: skipping source %s: %s", source.id, exc)
                continue

        if not chunks:
            raise IngestionError("ingestion produced no chunks from any source")
        return chunks
