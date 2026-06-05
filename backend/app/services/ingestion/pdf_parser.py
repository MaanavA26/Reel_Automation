"""PDF bytes → normalized text — the second parser behind the ingestion seam.

The HTML path (`parser.py`) uses the stdlib; PDF text extraction has no stdlib
equivalent, so `PypdfParser` wraps the lightweight pure-Python ``pypdf`` library.
That dependency cannot be installed in the offline build sandbox, so this module
is split to stay fully testable without it (ADR 0014):

- `normalize_pdf_text` — a pure function (page-list → joined, whitespace-collapsed
  text). Unit-tested directly, no ``pypdf`` needed.
- `PypdfParser` — the real adapter. It **lazy-imports** ``pypdf`` inside `parse`
  (never at construction), so default wiring works offline; a missing dependency
  surfaces as `ParseError` at parse time, which `IngestionService` tolerates as a
  per-source skip (graceful degradation). Live behaviour is covered by an
  ``@pytest.mark.integration`` test.

Per CLAUDE.md §4 this is deterministic *tool* work — no LLM. Scanned/image-only
PDFs (no text layer) yield empty text and raise `ParseError`; real text recovery
for them is the deferred OCR path (ADR 0014).
"""

from __future__ import annotations

import re

from app.services.ingestion.parser import ParseError

_WS = re.compile(r"\s+")


def normalize_pdf_text(pages: list[str]) -> str:
    """Join per-page text and collapse whitespace into one normalized string.

    Mirrors the HTML parser's normalization (collapsed whitespace) so chunks are
    shape-consistent regardless of source type. Blank pages are dropped; the
    result is stripped. Empty input (e.g. an image-only/scanned PDF with no text
    layer) yields ``""`` — the caller decides whether that is an error.
    """
    joined = " ".join(page for page in pages if page and page.strip())
    return _WS.sub(" ", joined).strip()


class PypdfParser:
    """A `PdfParser` backed by ``pypdf`` (lazy-imported; offline-safe to build)."""

    name = "pdf:pypdf"

    def parse(self, content: bytes) -> str:
        """Extract and normalize the text layer of a PDF.

        Raises `ParseError` if ``pypdf`` is unavailable, the bytes are not a
        parseable PDF, or the document carries no extractable text (e.g. a
        scanned/image-only PDF — recoverable only via the deferred OCR path).
        """
        try:
            import pypdf  # lazy: keeps default construction working offline
        except ImportError as exc:  # dependency not installed in this environment
            raise ParseError(f"pypdf is not installed: {exc}") from exc

        import io

        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # pypdf raises a variety of errors on bad input
            raise ParseError(f"could not parse PDF: {type(exc).__name__}: {exc}") from exc

        text = normalize_pdf_text(pages)
        if not text:
            raise ParseError("PDF yielded no extractable text (scanned/image-only?)")
        return text
