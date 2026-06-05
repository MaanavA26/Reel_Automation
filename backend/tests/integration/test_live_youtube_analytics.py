"""Live YouTube Analytics smoke test (requires a real OAuth token; skipped otherwise).

Run only this, after exporting a valid OAuth access token and a video id (from the
``backend/`` directory):

    REEL_YOUTUBE_ACCESS_TOKEN=... REEL_YOUTUBE_VIDEO_ID=... python -m pytest -m integration

It exercises the real path: YouTubeAnalyticsProvider -> live ``/v2/reports`` ->
a `VideoStats` carrying real (platform-measured) numbers. Skipped automatically
when the token/video id are absent, so the default `pytest` run never hits the
network. The token is read from the environment (not `Settings`) to keep the
analytics seam config-root-agnostic, mirroring the stock-visuals live test.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.analytics.youtube import YouTubeAnalyticsProvider

pytestmark = pytest.mark.integration


def test_youtube_analytics_against_live_api() -> None:
    token = os.environ.get("REEL_YOUTUBE_ACCESS_TOKEN")
    video_id = os.environ.get("REEL_YOUTUBE_VIDEO_ID")
    if not token or not video_id:
        pytest.skip("no live YouTube creds (set REEL_YOUTUBE_ACCESS_TOKEN + REEL_YOUTUBE_VIDEO_ID)")

    provider = YouTubeAnalyticsProvider(access_token=token)
    stats = asyncio.run(provider.fetch_stats(post_id=video_id))

    assert stats.post_id == video_id
    assert stats.views >= 0
    assert stats.fetched_via == "analytics:youtube"
