"""Tests for the analytics seam: `VideoStats` DTO + `FakeAnalyticsProvider`.

Fully offline. Mirrors `tests/services/search/test_fakes.py`.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.analytics.base import AnalyticsError, AnalyticsProvider, VideoStats
from app.analytics.fakes import FakeAnalyticsProvider


def _stats(post_id: str = "vid1", **overrides: object) -> VideoStats:
    defaults: dict[str, object] = {
        "post_id": post_id,
        "views": 100,
        "likes": 10,
        "estimated_minutes_watched": 50.0,
        "average_view_percentage": 60.0,
        "fetched_via": "analytics:fake",
    }
    defaults.update(overrides)
    return VideoStats(**defaults)  # type: ignore[arg-type]


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeAnalyticsProvider(), AnalyticsProvider)


def test_fake_replays_scripted_stats_and_records_calls() -> None:
    provider = FakeAnalyticsProvider({"vid1": _stats("vid1")})
    out = asyncio.run(provider.fetch_stats(post_id="vid1"))
    assert out.post_id == "vid1"
    assert out.views == 100
    assert provider.calls == ["vid1"]


def test_fake_unknown_post_id_raises_analytics_error() -> None:
    provider = FakeAnalyticsProvider({"vid1": _stats("vid1")})
    with pytest.raises(AnalyticsError):
        asyncio.run(provider.fetch_stats(post_id="missing"))
    assert provider.calls == ["missing"]  # still recorded


def test_video_stats_defaults_optional_metrics_to_none() -> None:
    s = VideoStats(post_id="v", views=1, likes=0, fetched_via="analytics:fake")
    assert s.estimated_minutes_watched is None
    assert s.average_view_percentage is None
    assert s.fetched_at.tzinfo is not None  # tz-aware UTC default


def test_video_stats_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        VideoStats(post_id="v", views=1, likes=0, fetched_via="x", surprise=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        VideoStats(post_id="v", views=-1, likes=0, fetched_via="x")
    with pytest.raises(ValidationError):
        VideoStats(post_id="v", views=1, likes=0, fetched_via="x", average_view_percentage=101.0)
