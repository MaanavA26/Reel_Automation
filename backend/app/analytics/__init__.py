"""Analytics / feedback loop — pull platform performance back to steer topics.

The fourth-layer band (CLAUDE.md §3.4): an `AnalyticsProvider` seam that fetches
per-video `VideoStats` (views, watch-time, retention, likes) for a platform post
id — mirroring the search fabric (protocol + hermetic fake + httpx adapter) — and
a deterministic `feedback` *tool* that ranks the topics that produced those videos
by an explainable performance score, closing the loop to the topic queue. Per
CLAUDE.md §4 both halves are deterministic tools/services; the *judgment* (which
ranked topics to pursue) stays with an upstream agent. See ADR 0036.
"""

from __future__ import annotations

from app.analytics.base import AnalyticsError, AnalyticsProvider, VideoStats
from app.analytics.fakes import FakeAnalyticsProvider
from app.analytics.feedback import TopicScore, score_topics
from app.analytics.youtube import YouTubeAnalyticsProvider

__all__ = [
    "AnalyticsError",
    "AnalyticsProvider",
    "FakeAnalyticsProvider",
    "TopicScore",
    "VideoStats",
    "YouTubeAnalyticsProvider",
    "score_topics",
]
