"""Tests for the hermetic `FakeTrendProvider`."""

from __future__ import annotations

import asyncio

from app.topics.base import TopicIdea
from app.topics.fakes import FakeTrendProvider


def _idea(title: str) -> TopicIdea:
    return TopicIdea(title=title, sourced_via="trends:fake")


def test_returns_flat_ideas_for_any_niche() -> None:
    ideas = [_idea("a"), _idea("b")]
    provider = FakeTrendProvider(ideas)
    out = asyncio.run(provider.discover(niche="tech"))
    assert [i.title for i in out] == ["a", "b"]


def test_per_niche_mapping_overrides_flat() -> None:
    provider = FakeTrendProvider(
        [_idea("default")],
        by_niche={"fitness": [_idea("squats"), _idea("protein")]},
    )
    assert [i.title for i in asyncio.run(provider.discover(niche="fitness"))] == [
        "squats",
        "protein",
    ]
    assert [i.title for i in asyncio.run(provider.discover(niche="cooking"))] == ["default"]


def test_limit_truncates() -> None:
    provider = FakeTrendProvider([_idea("a"), _idea("b"), _idea("c")])
    assert len(asyncio.run(provider.discover(niche="x", limit=2))) == 2


def test_records_calls() -> None:
    provider = FakeTrendProvider([_idea("a")])
    asyncio.run(provider.discover(niche="tech", limit=5))
    assert len(provider.calls) == 1
    assert provider.calls[0].niche == "tech"
    assert provider.calls[0].limit == 5
