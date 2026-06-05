"""Topic performance scoring — the analytics → topic-queue feedback tool.

This is the deterministic *tool* half of the feedback loop (CLAUDE.md §4): given
the `VideoStats` a platform reported and the topics that produced those videos,
it computes an **explainable** per-topic performance score so the highest-
performing topics can be re-fed to the content pipeline's topic queue. There is
no LLM here and no I/O — it is a pure function over already-fetched stats, so it
is trivially hermetic. Choosing *which* of the ranked topics to actually pursue
(and how to phrase the next video) is a judgment left to an upstream agent; this
tool only supplies the grounded numbers (§11, fact-vs-inference).

## Why the score is batch-independent (absolute), not batch-relative

A feedback loop re-runs over time as new videos publish. If the score min-max
normalized views across whatever happens to be in *this* batch, the same topic
would score differently run-to-run purely from its neighbors — confusing and
non-comparable across time, and undefined for a single topic. So every component
is **scale-free** and needs no batch:

- ``engagement_rate = min(likes / max(views, 1), 1.0)`` — a ratio in [0, 1]
  (clamped: on Shorts, view-counting can trail likes, so likes > views happens).
- ``avg_retention = average_view_percentage / 100`` — already a ratio in [0, 1].
- ``views_saturation = total_views / (total_views + VIEWS_REFERENCE)`` — a
  saturating transform of unbounded view counts into [0, 1), reading as "views
  relative to a reference scale of ``VIEWS_REFERENCE``." A documented constant,
  not a batch statistic.

The final ``score`` is a fixed, named-weight blend of the three, so it is total,
comparable across runs, and explainable: every `TopicScore` carries its component
breakdown alongside the number (the breakdown is what "explainable" means here).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.base import VideoStats

# --- Scoring weights (sum to 1.0) and the saturation reference, named constants
# so the ADR/readers can see and tune the policy without reading the math. ---
WEIGHT_VIEWS = 0.4
WEIGHT_RETENTION = 0.4
WEIGHT_ENGAGEMENT = 0.2
# Views at which views_saturation reaches 0.5 — a "what counts as a lot of views"
# reference scale. Tunable per channel maturity; documented, never batch-derived.
VIEWS_REFERENCE = 10_000.0


class TopicScore(BaseModel):
    """An explainable per-topic performance score (the feedback tool's output).

    Carries the final ``score`` *and* the component breakdown that produced it,
    plus ``video_count`` and the aggregated raw totals, so a reviewer (or an
    upstream topic-selection agent) can see *why* a topic ranks where it does —
    the §11 explainability requirement made structural. The components are each in
    [0, 1] and the score is their fixed-weight blend, also in [0, 1].
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    score: float = Field(ge=0.0, le=1.0)
    # Component breakdown (each scale-free, in [0, 1]):
    views_saturation: float = Field(ge=0.0, le=1.0)
    avg_retention: float = Field(ge=0.0, le=1.0)
    engagement_rate: float = Field(ge=0.0, le=1.0)
    # Aggregated raw evidence behind the components:
    video_count: int = Field(ge=0)
    total_views: int = Field(ge=0)
    total_likes: int = Field(ge=0)


def score_topics(
    stats_by_topic: Mapping[str, Sequence[VideoStats]],
) -> list[TopicScore]:
    """Score and rank topics by the performance of the videos they produced.

    ``stats_by_topic`` is pre-grouped by the caller (topic → its videos' stats),
    keeping the topic↔video association out of the platform-pure `VideoStats`.

    Returns a ranking, highest score first, with a **deterministic tie-break on
    the topic string** (ascending) so the order is stable run-to-run — mirroring
    the eval harness's lexicographic ranking idiom.

    Edge cases (all handled, none raise):
    - empty input → empty ranking;
    - a topic with **no** stats → scored 0.0 (kept, not dropped: "we made videos
      on this topic and they got no measured traction" is itself signal the queue
      should see, distinct from a topic never tried);
    - zero views → ``max(views, 1)`` guard, no divide-by-zero;
    - missing retention (``None``) → that video is **excluded from the retention
      average** (averaging a missing metric as 0 would understate, not absent).
    """
    scores = [_score_one(topic, list(stats)) for topic, stats in stats_by_topic.items()]
    # Total order: score desc, then topic asc as a stable, deterministic tie-break.
    scores.sort(key=lambda s: (-s.score, s.topic))
    return scores


def _score_one(topic: str, stats: list[VideoStats]) -> TopicScore:
    """Aggregate one topic's video stats into an explainable `TopicScore`."""
    total_views = sum(s.views for s in stats)
    total_likes = sum(s.likes for s in stats)

    # Absolute metrics sum across videos; the rate metric (retention) averages,
    # excluding videos whose retention the platform did not report.
    retentions = [s.average_view_percentage for s in stats if s.average_view_percentage is not None]
    avg_retention = (sum(retentions) / len(retentions) / 100.0) if retentions else 0.0

    # Clamp to [0, 1]: on YouTube Shorts (the primary platform) view-counting
    # trails likes early in a video's life — a view needs a watch threshold while
    # a like registers immediately — so likes > views is routine, not a corner
    # case. The rate saturates at 1.0 there rather than overflowing the DTO bound.
    engagement_rate = min(total_likes / max(total_views, 1), 1.0)
    views_saturation = total_views / (total_views + VIEWS_REFERENCE)

    score = (
        WEIGHT_VIEWS * views_saturation
        + WEIGHT_RETENTION * avg_retention
        + WEIGHT_ENGAGEMENT * engagement_rate
    )
    return TopicScore(
        topic=topic,
        score=score,
        views_saturation=views_saturation,
        avg_retention=avg_retention,
        engagement_rate=engagement_rate,
        video_count=len(stats),
        total_views=total_views,
        total_likes=total_likes,
    )
