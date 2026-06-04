"""Tests for the in-memory FakeSearchProvider (M5)."""

from __future__ import annotations

import asyncio

from app.schemas.research_state import SourceType
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider


def _result(url: str) -> SearchResult:
    return SearchResult(url=url, source_type=SourceType.WEB)


def test_returns_flat_results_for_any_query_and_records_calls() -> None:
    provider = FakeSearchProvider([_result("https://a.com"), _result("https://b.com")])
    hits = asyncio.run(provider.search(query="anything", limit=10))
    assert [h.url for h in hits] == ["https://a.com", "https://b.com"]
    assert provider.calls[0].query == "anything"


def test_respects_limit() -> None:
    provider = FakeSearchProvider([_result("https://a.com"), _result("https://b.com")])
    hits = asyncio.run(provider.search(query="q", limit=1))
    assert len(hits) == 1


def test_per_query_mapping() -> None:
    provider = FakeSearchProvider(by_query={"x": [_result("https://x.com")]})
    assert asyncio.run(provider.search(query="x"))[0].url == "https://x.com"
    assert asyncio.run(provider.search(query="unknown")) == []
