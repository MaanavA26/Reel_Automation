"""Live YouTube Shorts publish smoke test (requires real creds; skipped otherwise).

Unlike the read-only search/visuals smoke tests, this one is **side-effecting**:
it uploads a real video to a real channel. It is therefore gated on *both* a real
OAuth access token and a path to a real test video, and always uploads as
``private`` so nothing is posted publicly. It is skipped automatically unless both
are present, so the default ``pytest`` run (and CI) never hit the network.

Run only this, after exporting the creds (from the ``backend/`` directory):

    REEL_YOUTUBE_ACCESS_TOKEN=... REEL_YOUTUBE_TEST_VIDEO=/path/to/short.mp4 \
        python -m pytest -m integration -k youtube

The token must carry the ``youtube.upload`` OAuth scope and is the caller's to
refresh (this adapter assumes a currently-valid access token; see ADR 0033).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.media.schemas import RenderedVideo
from app.publishing.base import PublishTarget
from app.publishing.youtube import YouTubeShortsPublisher

pytestmark = pytest.mark.integration


def test_youtube_publish_against_live_api() -> None:
    token = os.environ.get("REEL_YOUTUBE_ACCESS_TOKEN")
    video_path = os.environ.get("REEL_YOUTUBE_TEST_VIDEO")
    if not token or not video_path:
        pytest.skip(
            "no live YouTube creds (set REEL_YOUTUBE_ACCESS_TOKEN + REEL_YOUTUBE_TEST_VIDEO)"
        )

    path = Path(video_path)
    publisher = YouTubeShortsPublisher(
        access_token=token,
        video_source=lambda _uri: path.read_bytes(),
    )
    video = RenderedVideo(
        video_uri=path.as_uri(),
        duration_ms=15_000,
        width=1080,
        height=1920,
        produced_via="composition:live-smoke",
    )
    target = PublishTarget(
        title="Reel Automation live smoke test",
        description="Automated publish smoke test.",
        tags=["test"],
        privacy_status="private",  # never public
    )

    result = asyncio.run(publisher.publish(video=video, target=target))

    assert result.platform == "youtube"
    assert result.post_id
    assert result.url.startswith("https://www.youtube.com/watch?v=")
