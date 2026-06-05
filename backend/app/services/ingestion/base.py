"""Provider-neutral contract for the fetch fabric.

A `FetchProvider` retrieves the raw bytes for a URL. Per CLAUDE.md §4 this is
deterministic IO (a tool/service), kept separate from the pure parser so the
parser is testable on fixture bytes with no network. Mirrors `SearchProvider`
in `app.services.search.base`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class FetchError(RuntimeError):
    """Raised when a URL cannot be retrieved (transport error, bad status, cap)."""


class FetchedContent(BaseModel):
    """Raw retrieved content for a URL — a transient DTO, not a persisted Chunk."""

    url: str
    content: bytes
    content_type: str | None = None


@runtime_checkable
class FetchProvider(Protocol):
    """A network backend that fetches the raw content of a URL.

    Async to match the workflow node contract (ADR 0002) — fetching is network
    I/O. Implementations harden the request (timeout, size/redirect caps,
    content-type allowlist) and never send credentials.
    """

    name: str

    async def fetch(self, *, url: str) -> FetchedContent: ...


@runtime_checkable
class PdfParser(Protocol):
    """A pure, synchronous PDF-bytes → normalized-text extractor.

    The second parser behind the ingestion seam (ADR 0014), alongside the stdlib
    `parse_html`. Per CLAUDE.md §4 this is deterministic *tool* work (no LLM):
    extract the text layer of a PDF and normalize it. Implementations raise
    `app.services.ingestion.parser.ParseError` when the bytes cannot be parsed
    (corrupt, encrypted, or — for the text-only v1 — an image-only/scanned PDF
    whose text must wait for the deferred OCR path). Synchronous because parsing
    is CPU-bound; the network I/O stays in `FetchProvider`.
    """

    name: str

    def parse(self, content: bytes) -> str: ...
