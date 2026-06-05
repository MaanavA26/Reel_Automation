"""Provider-neutral contract for the analytics / feedback loop.

An `AnalyticsProvider` turns a *platform post id* (a published video's id on
YouTube, Instagram, etc.) into a `VideoStats` snapshot — views, watch-time,
retention, likes. Per CLAUDE.md §3.4 this band pulls platform performance back
in to steer what gets made next; per §4 the *fetch* is deterministic
*tool/service* work (API-wrapping), never an LLM call. The judgment half — which
topics to make more of — lives in the `feedback` scorer downstream; this seam
only mints the raw, source-of-truth numbers.

Mirrors the search fabric (`app.services.search.base`) point-for-point: a
`@runtime_checkable` Protocol, a strict DTO, a hermetic fake, and a real
httpx-based adapter. The provider — never an LLM — is the only thing that mints a
`VideoStats`, keeping the same measured-fact-vs-inference boundary the search
fabric holds for `Source.url` (CLAUDE.md §11).

`VideoStats` carries no synthetic id: unlike a `Source` (discovered, so id-minted)
the platform already owns the identity — the `post_id` you queried with *is* the
natural key. It carries `fetched_via`/`fetched_at` provenance, symmetric with
`Source.discovered_via` / `VisualClip.produced_via`, so a stats snapshot records
which backend measured it and when.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsError(RuntimeError):
    """Raised when an analytics response cannot be parsed into `VideoStats`.

    Transport/status failures (timeout, 429, 5xx) are *not* wrapped — they
    propagate as ``httpx`` errors for the caller (an orchestrator/scheduler) to
    handle, mirroring the search-fabric error boundary (ADR 0021). Only an
    unparseable response *shape* is wrapped here. Defined in `base.py` (where the
    DTO lives) so the seam is self-contained, mirroring the visuals seam.
    """


class VideoStats(BaseModel):
    """A per-video performance snapshot for one platform post (the analytics DTO).

    Platform-pure: it holds only properties the platform reports. The topic that
    produced the video is *our* internal association, not a platform property, so
    it is deliberately **not** a field here — the topic↔video link is an input to
    the `feedback` scorer, kept out of this layer (clean band separation).

    Watch-time and retention are kept as distinct kinds, never collapsed:
    ``estimated_minutes_watched`` is *absolute* (total minutes), while
    ``average_view_percentage`` is a *ratio* (0-100, the fraction of the video an
    average viewer watched). Both are optional ``| None``: ``None`` means
    "the platform did not report this metric" — meaningfully different from ``0``.
    """

    model_config = ConfigDict(extra="forbid")

    post_id: str  # the platform's own video/post id — the natural key
    views: int = Field(ge=0)
    likes: int = Field(ge=0)
    # Absolute watch-time (total minutes watched across all views).
    estimated_minutes_watched: float | None = Field(default=None, ge=0.0)
    # Retention as a ratio: 0-100, the % of the video an average viewer watched.
    average_view_percentage: float | None = Field(default=None, ge=0.0, le=100.0)
    fetched_via: str  # provenance: "analytics:fake" / "analytics:youtube"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class AnalyticsProvider(Protocol):
    """A platform-analytics backend that returns stats for one published post.

    Async to match the network-I/O contract the rest of the repo's provider seams
    use (search/LLM/TTS). Implementations wrap a platform analytics API; the
    concrete adapter (`YouTubeAnalyticsProvider`) speaks one API behind this
    shared protocol, so adding a platform is a new adapter, not new agent code
    (CLAUDE.md §6, provider-neutral).

    Singular by post id (mirroring `SearchProvider.search(query=...)`): the caller
    holds the set of post ids it published and fetches them one at a time. A
    not-found post is an `AnalyticsError` (the adapter cannot honestly return a
    zeroed `VideoStats` for a post that does not exist).
    """

    name: str

    async def fetch_stats(self, *, post_id: str) -> VideoStats: ...
