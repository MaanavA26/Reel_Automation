"""Ingestion service — orchestrates fetch + parse + chunk over discovered sources.

A deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM): given the band's
`Source`s, it fetches each, parses to text by source type (HTML for WEB, the
text layer for PDF), and chunks. Per-source fetch/parse failures are tolerated
(skipped + logged); it raises `IngestionError` only when *no* chunks result at
all — mirroring the "never advance on empty acquisition" contract used by the
discovery agent. WEB landed in M6 (ADR 0008); PDF in M-LP (ADR 0014).
"""

from __future__ import annotations

import asyncio
import logging

from app.schemas.research_state import Chunk, Source, SourceType
from app.services.ingestion.base import FetchError, FetchProvider, PdfParser
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.parser import ParseError, parse_html
from app.services.ingestion.pdf_parser import PypdfParser
from app.services.ingestion.transcript import (
    TranscriptError,
    TranscriptProvider,
    normalize_transcript,
)

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when ingestion yields no chunks across all sources."""


class IngestionService:
    """Turns `Source`s into `Chunk`s via injected per-type providers.

    The `FetchProvider` handles WEB sources; the `PdfParser` handles PDF; the
    optional `TranscriptProvider` handles YOUTUBE. ``pdf_parser`` defaults to the
    real lazy ``pypdf`` adapter (safe to construct offline — a missing dependency
    surfaces as a per-source `ParseError` skip at ingest time, not here).
    ``transcript_provider`` defaults to ``None`` so the existing single-arg
    construction site (the graph's `ingest` node) is unchanged — when it is
    absent, YouTube sources fall through to the skip-and-log path.
    """

    def __init__(
        self,
        fetch_provider: FetchProvider,
        pdf_parser: PdfParser | None = None,
        *,
        transcript_provider: TranscriptProvider | None = None,
    ) -> None:
        self._fetch = fetch_provider
        self._pdf_parser: PdfParser = pdf_parser or PypdfParser()
        self._transcript = transcript_provider

    async def ingest(self, sources: list[Source]) -> list[Chunk]:
        """Fetch + parse + chunk each supported source; skip the rest and failures.

        WEB sources are HTML-parsed; PDF sources go through the injected
        `PdfParser` (text layer only — scanned/image PDFs await the deferred OCR
        path); YOUTUBE sources are transcribed + normalized (only when a
        `TranscriptProvider` is injected). Other source types are skipped.
        Per-source failures are tolerated (skipped + logged). Raises
        `IngestionError` if no chunk is produced from any source.
        """
        chunks: list[Chunk] = []
        for source in sources:
            try:
                if source.type is SourceType.WEB:
                    fetched = await self._fetch.fetch(url=source.url)
                    # parse_html is synchronous + CPU-bound; offload it to a worker
                    # thread so a large parse never blocks the event loop (and stalls
                    # other research jobs / status polls / health). Exceptions raised
                    # in the thread re-raise here, so the except block still catches.
                    text = await asyncio.to_thread(
                        parse_html, fetched.content, fetched.content_type
                    )
                elif source.type is SourceType.PDF:
                    fetched = await self._fetch.fetch(url=source.url)
                    # PdfParser.parse is synchronous + CPU-bound — offload it too.
                    text = await asyncio.to_thread(self._pdf_parser.parse, fetched.content)
                elif source.type is SourceType.YOUTUBE and self._transcript is not None:
                    segments = await self._transcript.fetch(url=source.url)
                    text = normalize_transcript(segments)
                else:
                    logger.info("ingestion: skipping source %s (%s)", source.id, source.type)
                    continue
                chunks.extend(chunk_text(text, source_id=source.id))
            except (FetchError, ParseError, TranscriptError) as exc:
                logger.warning("ingestion: skipping source %s: %s", source.id, exc)
                continue

        if not chunks:
            raise IngestionError("ingestion produced no chunks from any source")
        return chunks
