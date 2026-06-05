"""Tests for the YouTube Analytics provider adapter.

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building (endpoint, bearer auth, required date range, video filter) and
the column-name-keyed response → `VideoStats` mapping are verified without a live
call. The live call itself is a separate ``@pytest.mark.integration`` smoke test.
The wire contract asserted here mirrors the real ``GET /v2/reports`` payload
(``columnHeaders`` + ``rows``), verified against the reports.query reference.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.analytics.base import AnalyticsError, VideoStats
from app.analytics.youtube import YouTubeAnalyticsProvider


def _provider(handler: Any, **kwargs: Any) -> YouTubeAnalyticsProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return YouTubeAnalyticsProvider(access_token="tok", client=client, **kwargs)


def _report(*names_and_values: tuple[str, Any]) -> httpx.Response:
    headers = [{"name": n, "dataType": "INTEGER"} for n, _ in names_and_values]
    row = [v for _, v in names_and_values]
    return httpx.Response(200, json={"columnHeaders": headers, "rows": [row]})


def _fetch(provider: YouTubeAnalyticsProvider, *, post_id: str = "vid1") -> VideoStats:
    return asyncio.run(provider.fetch_stats(post_id=post_id))


def test_builds_request_and_maps_response_by_column_name() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url).split("?")[0]
        seen["auth"] = request.headers.get("Authorization")
        seen["params"] = dict(request.url.params)
        # Deliberately NOT in metric-declaration order — proves name-keyed mapping.
        return _report(
            ("likes", 10),
            ("views", 100),
            ("averageViewPercentage", 55.5),
            ("estimatedMinutesWatched", 42.0),
        )

    out = _fetch(_provider(handler, end_date="2026-06-01"))
    assert seen["url"] == "https://youtubeanalytics.googleapis.com/v2/reports"
    assert seen["auth"] == "Bearer tok"
    assert seen["params"]["ids"] == "channel==MINE"
    assert seen["params"]["filters"] == "video==vid1"
    assert seen["params"]["startDate"] == "2005-02-14"
    assert seen["params"]["endDate"] == "2026-06-01"
    assert seen["params"]["metrics"] == "views,likes,estimatedMinutesWatched,averageViewPercentage"
    assert out == VideoStats(
        post_id="vid1",
        views=100,
        likes=10,
        estimated_minutes_watched=42.0,
        average_view_percentage=55.5,
        fetched_via="analytics:youtube",
        fetched_at=out.fetched_at,
    )


def test_end_date_defaults_to_today_when_unset() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return _report(("views", 1), ("likes", 0))

    _fetch(_provider(handler))
    # Set per-call; assert it is a well-formed YYYY-MM-DD, not a frozen literal.
    assert len(seen["params"]["endDate"]) == 10
    assert seen["params"]["endDate"].count("-") == 2


def test_empty_rows_is_not_found() -> None:
    handler = lambda r: httpx.Response(200, json={"columnHeaders": [], "rows": []})  # noqa: E731
    with pytest.raises(AnalyticsError, match="not found"):
        _fetch(_provider(handler))


def test_absent_rows_is_not_found() -> None:
    handler = lambda r: httpx.Response(200, json={"columnHeaders": []})  # noqa: E731
    with pytest.raises(AnalyticsError, match="not found"):
        _fetch(_provider(handler))


def test_missing_required_metric_column_raises() -> None:
    handler = lambda r: _report(("views", 100))  # no 'likes'  # noqa: E731
    with pytest.raises(AnalyticsError, match="likes"):
        _fetch(_provider(handler))


def test_optional_metrics_may_be_absent() -> None:
    handler = lambda r: _report(("views", 100), ("likes", 5))  # noqa: E731
    out = _fetch(_provider(handler))
    assert out.estimated_minutes_watched is None
    assert out.average_view_percentage is None


def test_malformed_shape_raises_analytics_error() -> None:
    handler = lambda r: httpx.Response(200, json=["not", "a", "dict"])  # noqa: E731
    with pytest.raises(AnalyticsError):
        _fetch(_provider(handler))


def test_http_error_propagates_not_wrapped() -> None:
    handler = lambda r: httpx.Response(403, json={"error": "forbidden"})  # noqa: E731
    with pytest.raises(httpx.HTTPStatusError):
        _fetch(_provider(handler))


def test_empty_token_raises() -> None:
    with pytest.raises(AnalyticsError):
        YouTubeAnalyticsProvider(access_token="")


def test_token_never_leaks_in_repr() -> None:
    provider = YouTubeAnalyticsProvider(access_token="super-secret-token")
    assert "super-secret-token" not in repr(provider)
