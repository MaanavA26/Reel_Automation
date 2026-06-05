"""In-memory `AnalyticsProvider` for hermetic tests (no network).

A factory-style fake (testing-standards: "don't mock what you can fake") that
replays scripted `VideoStats` per post id and records the post ids it was asked
for. Mirrors `app.services.search.fakes.FakeSearchProvider`.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.analytics.base import AnalyticsError, VideoStats

PROVIDER_NAME = "fake"


class FakeAnalyticsProvider:
    """An `AnalyticsProvider` that replays scripted stats by post id.

    Construct with a ``post_id -> VideoStats`` mapping. Records each requested
    post id for assertions. Raises `AnalyticsError` for an unknown post id,
    mirroring a real backend's not-found behavior (the seam contract).
    """

    name = PROVIDER_NAME

    def __init__(self, stats: Mapping[str, VideoStats] | None = None) -> None:
        self._stats: dict[str, VideoStats] = dict(stats or {})
        self.calls: list[str] = []

    async def fetch_stats(self, *, post_id: str) -> VideoStats:
        self.calls.append(post_id)
        try:
            return self._stats[post_id]
        except KeyError:
            raise AnalyticsError(f"no scripted stats for post_id={post_id!r}") from None
