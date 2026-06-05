"""Tests for the PDF parser (M-LP / ADR 0014).

The pure ``normalize_pdf_text`` function and the offline ``FakePdfParser`` path
are hermetic. ``PypdfParser``'s real extraction needs the ``pypdf`` dependency
(absent in the offline sandbox), so its happy-path test is ``@pytest.mark.integration``;
its dependency-missing degradation is covered hermetically here.
"""

from __future__ import annotations

import importlib.util

import pytest

from app.services.ingestion.fakes import FakePdfParser
from app.services.ingestion.parser import ParseError
from app.services.ingestion.pdf_parser import PypdfParser, normalize_pdf_text

_HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


def test_normalize_joins_pages_and_collapses_whitespace() -> None:
    assert normalize_pdf_text(["Hello\n\n  world", "second   page"]) == "Hello world second page"


def test_normalize_drops_blank_pages_and_strips() -> None:
    assert normalize_pdf_text(["  ", "", "  text  ", "   "]) == "text"


def test_normalize_empty_input_yields_empty_string() -> None:
    assert normalize_pdf_text([]) == ""


def test_fake_pdf_parser_returns_scripted_text() -> None:
    parser = FakePdfParser({b"%PDF-bytes": "extracted body"})
    assert parser.parse(b"%PDF-bytes") == "extracted body"


def test_fake_pdf_parser_unmapped_bytes_raise_parse_error() -> None:
    with pytest.raises(ParseError):
        FakePdfParser().parse(b"unknown")


def test_pypdf_parser_missing_dependency_raises_parse_error() -> None:
    """Offline (no ``pypdf``): the lazy import fails as a `ParseError`, not at init."""
    if _HAS_PYPDF:
        pytest.skip("pypdf is installed; dependency-missing path not exercisable")
    parser = PypdfParser()  # construction must not raise even without the dep
    with pytest.raises(ParseError, match="pypdf is not installed"):
        parser.parse(b"%PDF-1.4 anything")


@pytest.mark.integration
def test_pypdf_parser_blank_page_raises_no_text_error() -> None:
    """Live (real ``pypdf``): a text-layer-less page mirrors the scanned-PDF case.

    Exercises the real `pypdf.PdfReader`/`.pages`/`.extract_text` path end-to-end
    and asserts the empty-extraction branch. The text-bearing happy path is left
    uncovered: a hermetic text PDF fixture cannot be verified in the offline
    sandbox, so it is deferred to a network-enabled run rather than asserted
    against an unverifiable fixture. Run with ``-m integration``.
    """
    if not _HAS_PYPDF:
        pytest.skip("pypdf not installed in this environment")
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ParseError, match="no extractable text"):
        PypdfParser().parse(buf.getvalue())
