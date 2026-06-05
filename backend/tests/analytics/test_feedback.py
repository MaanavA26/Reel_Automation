"""Tests for the `feedback.score_topics` topic-scoring tool.

Pure function, fully hermetic. Asserts the explainable-score contract: scale-free
components, batch-independence, deterministic ranking + tie-break, and the
edge cases (empty input, zero views, missing retention, single topic).
"""

from __future__ import annotations

from app.analytics.base import VideoStats
from app.analytics.feedback import (
    VIEWS_REFERENCE,
    WEIGHT_ENGAGEMENT,
    WEIGHT_RETENTION,
    WEIGHT_VIEWS,
    score_topics,
)


def _stats(
    post_id: str,
    *,
    views: int,
    likes: int,
    retention: float | None = None,
) -> VideoStats:
    return VideoStats(
        post_id=post_id,
        views=views,
        likes=likes,
        average_view_percentage=retention,
        fetched_via="analytics:fake",
    )


def test_empty_input_is_empty_ranking() -> None:
    assert score_topics({}) == []


def test_topic_with_no_stats_scores_zero_and_is_kept() -> None:
    out = score_topics({"dead topic": []})
    assert len(out) == 1
    assert out[0].topic == "dead topic"
    assert out[0].score == 0.0
    assert out[0].video_count == 0


def test_components_are_scale_free_and_aggregate_correctly() -> None:
    out = score_topics({"t": [_stats("a", views=1000, likes=100, retention=80.0)]})
    s = out[0]
    assert s.total_views == 1000
    assert s.total_likes == 100
    assert s.engagement_rate == 100 / 1000
    assert s.avg_retention == 0.80
    assert s.views_saturation == 1000 / (1000 + VIEWS_REFERENCE)
    expected = (
        WEIGHT_VIEWS * s.views_saturation
        + WEIGHT_RETENTION * s.avg_retention
        + WEIGHT_ENGAGEMENT * s.engagement_rate
    )
    assert abs(s.score - expected) < 1e-12


def test_zero_views_no_divide_by_zero() -> None:
    out = score_topics({"t": [_stats("a", views=0, likes=0)]})
    assert out[0].engagement_rate == 0.0
    assert out[0].views_saturation == 0.0
    assert out[0].score == 0.0


def test_missing_retention_excluded_from_average() -> None:
    # Two videos, only one reports retention -> average is that one, not halved.
    out = score_topics(
        {"t": [_stats("a", views=10, likes=1, retention=90.0), _stats("b", views=10, likes=1)]}
    )
    assert out[0].avg_retention == 0.90


def test_all_retention_missing_is_zero() -> None:
    out = score_topics({"t": [_stats("a", views=10, likes=1)]})
    assert out[0].avg_retention == 0.0


def test_ranking_is_descending_by_score() -> None:
    out = score_topics(
        {
            "low": [_stats("a", views=10, likes=0, retention=10.0)],
            "high": [_stats("b", views=50_000, likes=5_000, retention=95.0)],
        }
    )
    assert [s.topic for s in out] == ["high", "low"]
    assert out[0].score > out[1].score


def test_tie_break_is_topic_ascending_and_deterministic() -> None:
    # Identical stats -> identical scores -> tie broken by topic string ascending.
    same = dict(views=100, likes=10, retention=50.0)
    out = score_topics(
        {
            "zebra": [_stats("z", **same)],
            "apple": [_stats("a", **same)],
            "mango": [_stats("m", **same)],
        }
    )
    assert [s.topic for s in out] == ["apple", "mango", "zebra"]


def test_batch_independence_single_topic_same_score() -> None:
    # A topic's score must not depend on what else is in the batch.
    topic_stats = [_stats("a", views=1000, likes=100, retention=70.0)]
    alone = score_topics({"t": topic_stats})
    with_neighbor = score_topics(
        {"t": topic_stats, "other": [_stats("b", views=999_999, likes=999_999, retention=99.0)]}
    )
    t_alone = alone[0]
    t_neighbor = next(s for s in with_neighbor if s.topic == "t")
    assert t_alone.score == t_neighbor.score


def test_likes_exceeding_views_clamps_engagement_to_one() -> None:
    # YouTube Shorts: view-counting trails likes early, so likes > views (and
    # views == 0 with likes > 0) is routine. Must not raise; rate saturates at 1.
    out = score_topics({"t": [_stats("a", views=0, likes=5)]})
    assert out[0].engagement_rate == 1.0
    assert 0.0 <= out[0].score <= 1.0

    out2 = score_topics({"t": [_stats("a", views=3, likes=9)]})
    assert out2[0].engagement_rate == 1.0


def test_score_is_bounded_unit_interval() -> None:
    out = score_topics({"t": [_stats("a", views=10**9, likes=10**9, retention=100.0)]})
    assert 0.0 <= out[0].score <= 1.0
