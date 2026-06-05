"""Tests for the TikTok / Instagram Reels publisher skeletons.

They conform to the `PublishingProvider` protocol but `publish` raises a clear
`PublishError` ("adapter pending") until their concrete API integrations land.
"""

from __future__ import annotations

import asyncio

import pytest

from app.media.schemas import RenderedVideo
from app.publishing.base import PublishingProvider, PublishTarget
from app.publishing.skeletons import InstagramReelsPublisher, TikTokPublisher
from app.publishing.youtube import PublishError


def _video() -> RenderedVideo:
    return RenderedVideo(
        video_uri="file:///tmp/short.mp4",
        duration_ms=30_000,
        width=1080,
        height=1920,
        produced_via="composition:fake",
    )


@pytest.mark.parametrize("cls", [TikTokPublisher, InstagramReelsPublisher])
def test_skeleton_conforms_to_protocol(cls: type) -> None:
    assert isinstance(cls(), PublishingProvider)


@pytest.mark.parametrize("cls", [TikTokPublisher, InstagramReelsPublisher])
def test_skeleton_raises_adapter_pending(cls: type) -> None:
    provider = cls()
    with pytest.raises(PublishError, match="pending"):
        asyncio.run(provider.publish(video=_video(), target=PublishTarget(title="t")))
