"""Tests for the `TopicIdea` DTO and `TrendProvider` protocol conformance."""

from __future__ import annotations

import pytest

from app.topics.base import TopicIdea, TrendProvider
from app.topics.fakes import FakeTrendProvider


def test_topic_idea_mints_prefixed_id() -> None:
    idea = TopicIdea(title="AI agents", sourced_via="trends:fake")
    assert idea.id.startswith("topic_")
    assert idea.signal is None  # optional, defaults to None
    assert idea.sourced_at is not None


def test_topic_idea_ids_are_unique() -> None:
    a = TopicIdea(title="a", sourced_via="trends:fake")
    b = TopicIdea(title="b", sourced_via="trends:fake")
    assert a.id != b.id


def test_topic_idea_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        TopicIdea(title="x", sourced_via="trends:fake", bogus="nope")  # type: ignore[call-arg]


def test_fake_satisfies_trend_provider_protocol() -> None:
    provider = FakeTrendProvider()
    assert isinstance(provider, TrendProvider)
