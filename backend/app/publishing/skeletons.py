"""Protocol-conformant skeleton publishers for TikTok and Instagram Reels.

These are placeholders that satisfy the `PublishingProvider` protocol (so they
slot into a registry / DI site today) but `publish` raises `PublishError`
(``"adapter pending"``) until the concrete API integration lands — the
twice-blessed "scaffold the seam, defer the network adapter" move (ADR 0019 /
0024), here applied to the publishing band.

TikTok (Content Posting API) and Instagram (Graph API Content Publishing) each
use a multi-step *create container → publish* flow distinct from YouTube's
resumable upload; each will become its own concrete adapter behind this same
protocol when implemented. Keeping them as explicit, named skeletons (rather
than omitting them) documents the intended platform coverage of CLAUDE.md §3.4.
"""

from __future__ import annotations

from app.media.schemas import RenderedVideo
from app.publishing.base import PublishResult, PublishTarget
from app.publishing.youtube import PublishError


class TikTokPublisher:
    """A `PublishingProvider` skeleton for TikTok (adapter pending).

    Conforms to the protocol so wiring/registration can reference it today; the
    real TikTok Content Posting API integration is deferred (ADR 0033).
    """

    name = "tiktok"

    async def publish(self, *, video: RenderedVideo, target: PublishTarget) -> PublishResult:
        raise PublishError("TikTok publisher adapter pending")


class InstagramReelsPublisher:
    """A `PublishingProvider` skeleton for Instagram Reels (adapter pending).

    Conforms to the protocol so wiring/registration can reference it today; the
    real Instagram Graph API content-publishing integration is deferred (ADR 0033).
    """

    name = "instagram_reels"

    async def publish(self, *, video: RenderedVideo, target: PublishTarget) -> PublishResult:
        raise PublishError("Instagram Reels publisher adapter pending")
