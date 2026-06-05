"""Tests for the IngestionService orchestration (M6 web, M-LP/ADR 0014 PDF)."""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.research_state import Source, SourceType
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider, FakePdfParser
from app.services.ingestion.service import IngestionError, IngestionService


def _web(url: str) -> Source:
    return Source(url=url, type=SourceType.WEB, discovered_via="search:fake")


def _pdf(url: str) -> Source:
    return Source(url=url, type=SourceType.PDF, discovered_via="search:fake")


def _html(url: str, body: str) -> FetchedContent:
    return FetchedContent(url=url, content=f"<p>{body}</p>".encode(), content_type="text/html")


def _pdf_bytes(url: str, raw: bytes) -> FetchedContent:
    return FetchedContent(url=url, content=raw, content_type="application/pdf")


def test_ingests_web_sources_to_chunks() -> None:
    a, b = _web("https://a.com"), _web("https://b.com")
    fetch = FakeFetchProvider(
        {
            "https://a.com": _html("https://a.com", "alpha"),
            "https://b.com": _html("https://b.com", "beta"),
        }
    )
    chunks = asyncio.run(IngestionService(fetch).ingest([a, b]))
    assert {c.source_id for c in chunks} == {a.id, b.id}
    joined = " ".join(c.text for c in chunks)
    assert "alpha" in joined and "beta" in joined


def test_failed_source_is_skipped_but_others_continue() -> None:
    good, bad = _web("https://good.com"), _web("https://bad.com")  # bad.com unmapped → FetchError
    fetch = FakeFetchProvider({"https://good.com": _html("https://good.com", "ok")})
    chunks = asyncio.run(IngestionService(fetch).ingest([good, bad]))
    assert chunks and all(c.source_id == good.id for c in chunks)


def test_ingests_pdf_sources_via_injected_parser() -> None:
    pdf = _pdf("https://x.com/f.pdf")
    raw = b"%PDF-1.4 fixture"
    fetch = FakeFetchProvider({pdf.url: _pdf_bytes(pdf.url, raw)})
    parser = FakePdfParser({raw: "paper body text"})
    chunks = asyncio.run(IngestionService(fetch, parser).ingest([pdf]))
    assert chunks and all(c.source_id == pdf.id for c in chunks)
    assert "paper body text" in " ".join(c.text for c in chunks)


def test_mixed_web_and_pdf_sources_both_ingest() -> None:
    web, pdf = _web("https://a.com"), _pdf("https://x.com/f.pdf")
    raw = b"%PDF-1.4 fixture"
    fetch = FakeFetchProvider({web.url: _html(web.url, "alpha"), pdf.url: _pdf_bytes(pdf.url, raw)})
    parser = FakePdfParser({raw: "beta"})
    chunks = asyncio.run(IngestionService(fetch, parser).ingest([web, pdf]))
    assert {c.source_id for c in chunks} == {web.id, pdf.id}
    joined = " ".join(c.text for c in chunks)
    assert "alpha" in joined and "beta" in joined


def test_unparseable_pdf_is_skipped_but_others_continue() -> None:
    web, pdf = _web("https://a.com"), _pdf("https://x.com/f.pdf")
    fetch = FakeFetchProvider(
        {web.url: _html(web.url, "ok"), pdf.url: _pdf_bytes(pdf.url, b"%PDF-bad")}
    )
    parser = FakePdfParser()  # no scripted text → ParseError → skip
    chunks = asyncio.run(IngestionService(fetch, parser).ingest([web, pdf]))
    assert chunks and all(c.source_id == web.id for c in chunks)


def test_unsupported_source_types_are_skipped_not_fetched() -> None:
    web = _web("https://a.com")
    yt = Source(url="https://youtube.com/v", type=SourceType.YOUTUBE, discovered_via="search:fake")
    fetch = FakeFetchProvider({"https://a.com": _html("https://a.com", "content")})
    chunks = asyncio.run(IngestionService(fetch).ingest([web, yt]))
    assert all(c.source_id == web.id for c in chunks)
    assert all(call.url != yt.url for call in fetch.calls)  # youtube never fetched


def test_zero_chunks_raises_ingestion_error() -> None:
    yt = Source(url="https://youtube.com/v", type=SourceType.YOUTUBE, discovered_via="search:fake")
    with pytest.raises(IngestionError):
        asyncio.run(IngestionService(FakeFetchProvider({})).ingest([yt]))
