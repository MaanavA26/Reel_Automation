"""Tests for the `HttpTrendProvider` live adapter (offline via MockTransport).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building (endpoint, ``X-Api-Key`` auth, params) and the
response → `TopicIdea` mapping are verified without a live call. The live call
itself is covered by a separate ``@pytest.mark.integration`` smoke test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.topics.live import PROVIDER_NAME, HttpTrendProvider, TrendError


def _provider(handler: Any, **kwargs: Any) -> HttpTrendProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTrendProvider(api_key="k", client=client, **kwargs)


def _trends(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"trends": list(items)})


def _discover(provider: HttpTrendProvider, *, niche: str = "tech", limit: int = 10) -> Any:
    return asyncio.run(provider.discover(niche=niche, limit=limit))


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url).split("?")[0]
        seen["key"] = request.headers.get("X-Api-Key")
        seen["accept"] = request.headers.get("Accept")
        seen["params"] = dict(request.url.params)
        return _trends(
            {
                "keyword": "ai agents",
                "title": "AI agents explode",
                "score": 91.0,
                "url": "https://t.com/ai",
            },
            {"keyword": "rag", "score": 70},
        )

    out = _discover(_provider(handler), niche="tech")
    assert seen["url"] == "https://api.example-trends.com/trends"
    assert seen["key"] == "k"
    assert seen["accept"] == "application/json"
    assert seen["params"] == {"q": "tech", "limit": "10"}

    assert out[0].title == "AI agents explode"
    assert out[0].keyword == "ai agents"
    assert out[0].signal == 91.0
    assert out[0].url == "https://t.com/ai"
    assert out[0].niche == "tech"
    assert out[0].sourced_via == f"trends:{PROVIDER_NAME}"
    # title falls back to keyword when absent
    assert out[1].title == "rag"
    assert out[1].signal == 70.0


def test_limit_truncates_results() -> None:
    handler = lambda r: _trends(  # noqa: E731
        {"keyword": "a"}, {"keyword": "b"}, {"keyword": "c"}
    )
    out = _discover(_provider(handler), limit=2)
    assert [i.keyword for i in out] == ["a", "b"]


def test_skips_items_without_title_or_keyword() -> None:
    handler = lambda r: _trends(  # noqa: E731
        {"score": 99}, {"keyword": "", "title": ""}, {"keyword": "ok"}
    )
    out = _discover(_provider(handler))
    assert [i.keyword for i in out] == ["ok"]


def test_garbage_score_becomes_none() -> None:
    out = _discover(_provider(lambda r: _trends({"keyword": "a", "score": "lots"})))
    assert out[0].signal is None


def test_missing_trends_key_is_empty_not_error() -> None:
    out = _discover(_provider(lambda r: httpx.Response(200, json={"other": 1})))
    assert out == []


def test_malformed_trends_shape_raises() -> None:
    with pytest.raises(TrendError):
        _discover(_provider(lambda r: httpx.Response(200, json={"trends": "nope"})))


def test_malformed_top_level_shape_raises() -> None:
    with pytest.raises(TrendError):
        _discover(_provider(lambda r: httpx.Response(200, json=["not", "a", "dict"])))


def test_http_error_surfaces() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _discover(_provider(lambda r: httpx.Response(429, json={"error": "rate limited"})))


def test_missing_api_key_raises() -> None:
    with pytest.raises(TrendError):
        HttpTrendProvider(api_key="")


def test_key_not_leaked_in_repr() -> None:
    provider = HttpTrendProvider(api_key="super-secret-token")
    assert "super-secret-token" not in repr(provider)
