"""Tests for the IngestionService orchestration (M6)."""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.research_state import Source, SourceType
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionError, IngestionService


def _web(url: str) -> Source:
    return Source(url=url, type=SourceType.WEB, discovered_via="search:fake")


def _html(url: str, body: str) -> FetchedContent:
    return FetchedContent(url=url, content=f"<p>{body}</p>".encode(), content_type="text/html")


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


def test_non_web_sources_are_skipped_not_fetched() -> None:
    web = _web("https://a.com")
    pdf = Source(url="https://x.com/f.pdf", type=SourceType.PDF, discovered_via="search:fake")
    fetch = FakeFetchProvider({"https://a.com": _html("https://a.com", "content")})
    chunks = asyncio.run(IngestionService(fetch).ingest([web, pdf]))
    assert all(c.source_id == web.id for c in chunks)
    assert all(call.url != pdf.url for call in fetch.calls)  # pdf never fetched


def test_zero_chunks_raises_ingestion_error() -> None:
    pdf = Source(url="https://x.com/f.pdf", type=SourceType.PDF, discovered_via="search:fake")
    with pytest.raises(IngestionError):
        asyncio.run(IngestionService(FakeFetchProvider({})).ingest([pdf]))
