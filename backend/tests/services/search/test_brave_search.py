"""Tests for the Brave Search provider adapter (M-LP.2).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building (endpoint, ``X-Subscription-Token`` auth, ``count`` clamp) and
the response → `SearchResult` mapping are verified without a live call. The live
call itself is covered by a separate ``@pytest.mark.integration`` smoke test
(skipped without a key). The Brave wire contract asserted here mirrors the real
``GET /res/v1/web/search`` payload (``web.results[]`` with ``url``/``title``/
``description``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.schemas.research_state import SourceType
from app.services.search.brave_search import BraveSearchProvider, SearchError


def _provider(handler: Any, **kwargs: Any) -> BraveSearchProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BraveSearchProvider(api_key="k", client=client, **kwargs)


def _web(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"web": {"results": list(results)}})


def _search(provider: BraveSearchProvider, *, query: str = "q", limit: int = 10) -> Any:
    return asyncio.run(provider.search(query=query, limit=limit))


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url).split("?")[0]
        seen["token"] = request.headers.get("X-Subscription-Token")
        seen["accept"] = request.headers.get("Accept")
        seen["params"] = dict(request.url.params)
        return _web(
            {"url": "https://a.com", "title": "A", "description": "snippet a"},
            {"url": "https://b.com", "title": "B", "description": "snippet b"},
        )

    out = _search(_provider(handler), query="cats")
    assert seen["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert seen["token"] == "k"
    assert seen["accept"] == "application/json"
    assert seen["params"] == {"q": "cats", "count": "10"}
    assert [r.url for r in out] == ["https://a.com", "https://b.com"]
    assert out[0].source_type is SourceType.WEB
    assert out[0].title == "A"
    assert out[0].snippet == "snippet a"  # Brave's `description` maps to snippet


def test_count_clamped_to_max_20() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return _web()

    _search(_provider(handler), limit=50)
    assert seen["params"]["count"] == "20"  # Brave 422s above 20


def test_limit_truncates_results() -> None:
    handler = lambda r: _web(  # noqa: E731
        {"url": "https://a.com"}, {"url": "https://b.com"}, {"url": "https://c.com"}
    )
    out = _search(_provider(handler), limit=2)
    assert [r.url for r in out] == ["https://a.com", "https://b.com"]


def test_skips_results_without_url() -> None:
    handler = lambda r: _web(  # noqa: E731
        {"title": "no url"}, {"url": "", "title": "empty"}, {"url": "https://ok.com"}
    )
    out = _search(_provider(handler))
    assert [r.url for r in out] == ["https://ok.com"]


def test_missing_web_block_is_empty_not_error() -> None:
    out = _search(_provider(lambda r: httpx.Response(200, json={"query": {}})))
    assert out == []


def test_missing_results_list_is_empty_not_error() -> None:
    out = _search(_provider(lambda r: httpx.Response(200, json={"web": {}})))
    assert out == []


def test_malformed_results_shape_raises_search_error() -> None:
    with pytest.raises(SearchError):
        _search(_provider(lambda r: httpx.Response(200, json={"web": {"results": "nope"}})))


def test_malformed_top_level_shape_raises_search_error() -> None:
    with pytest.raises(SearchError):
        _search(_provider(lambda r: httpx.Response(200, json=["not", "a", "dict"])))


def test_http_error_surfaces() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _search(_provider(lambda r: httpx.Response(429, json={"error": "rate limited"})))


def test_missing_api_key_raises() -> None:
    with pytest.raises(SearchError):
        BraveSearchProvider(api_key="")


def test_key_not_leaked_in_repr() -> None:
    provider = BraveSearchProvider(api_key="super-secret-token")
    assert "super-secret-token" not in repr(provider)
