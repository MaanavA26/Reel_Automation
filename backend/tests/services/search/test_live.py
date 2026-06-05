"""Tests for the live Tavily `SearchProvider` adapter (M-LP.2).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building and the response → `SearchResult` mapping are verified without a
live call. The live call itself is covered by a separate
``@pytest.mark.integration`` smoke test (skipped without a key).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.schemas.research_state import SourceType
from app.services.search.base import SearchResult
from app.services.search.live import SearchError, TavilySearchProvider


def _provider(handler: Any) -> TavilySearchProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TavilySearchProvider(api_key="tvly-k", client=client)


def _resp(results: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"results": results})


def _search(
    provider: TavilySearchProvider, *, query: str = "q", limit: int = 10
) -> list[SearchResult]:
    return asyncio.run(provider.search(query=query, limit=limit))


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return _resp([{"url": "https://a.com", "title": "A", "content": "snippet a"}])

    hits = _search(_provider(handler), query="vaccines", limit=5)
    assert hits == [
        SearchResult(
            url="https://a.com",
            source_type=SourceType.WEB,
            title="A",
            snippet="snippet a",
        )
    ]
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["auth"] == "Bearer tvly-k"
    assert seen["body"]["query"] == "vaccines"
    assert seen["body"]["max_results"] == 5


def test_maps_missing_optional_fields_to_none() -> None:
    hits = _search(_provider(lambda r: _resp([{"url": "https://a.com"}])))
    assert hits == [SearchResult(url="https://a.com", source_type=SourceType.WEB)]


def test_skips_results_without_url() -> None:
    handler = lambda r: _resp([{"title": "no url"}, {"url": "https://b.com"}])  # noqa: E731
    hits = _search(_provider(handler))
    assert [h.url for h in hits] == ["https://b.com"]


def test_respects_limit() -> None:
    handler = lambda r: _resp(  # noqa: E731
        [{"url": "https://a.com"}, {"url": "https://b.com"}, {"url": "https://c.com"}]
    )
    hits = _search(_provider(handler), limit=2)
    assert len(hits) == 2


def test_empty_results_is_valid() -> None:
    assert _search(_provider(lambda r: _resp([]))) == []


def test_bad_response_shape_raises_search_error() -> None:
    with pytest.raises(SearchError):
        _search(_provider(lambda r: httpx.Response(200, json={"unexpected": True})))


def test_non_list_results_raises_search_error() -> None:
    with pytest.raises(SearchError):
        _search(_provider(lambda r: httpx.Response(200, json={"results": "nope"})))


def test_http_error_surfaces() -> None:
    # Operational failures propagate as httpx errors (not swallowed); the
    # Orchestrator owns retries/budgets (ADR 0007).
    with pytest.raises(httpx.HTTPStatusError):
        _search(_provider(lambda r: httpx.Response(429, json={"error": "rate limited"})))


def test_missing_api_key_raises() -> None:
    with pytest.raises(SearchError):
        TavilySearchProvider(api_key="")


def test_api_key_never_leaks_in_repr() -> None:
    provider = TavilySearchProvider(api_key="tvly-secret")
    assert "tvly-secret" not in repr(provider)
