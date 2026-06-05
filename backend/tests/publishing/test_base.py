"""Tests for the publishing band's DTOs and hermetic fake.

Fully offline: no network, no platform. Verifies the `PublishTarget` /
`PublishResult` DTO contracts (strict, id-prefixed, defaults) and that
`FakePublishingProvider` conforms to the protocol, records calls, and mints
deterministic provenance.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.media.schemas import RenderedVideo
from app.publishing.base import (
    FakePublishingProvider,
    PublishingProvider,
    PublishResult,
    PublishTarget,
)


def _video() -> RenderedVideo:
    return RenderedVideo(
        video_uri="file:///tmp/short.mp4",
        duration_ms=30_000,
        width=1080,
        height=1920,
        produced_via="composition:fake",
    )


def test_publish_target_defaults() -> None:
    target = PublishTarget(title="Hello")
    assert target.description == ""
    assert target.tags == []
    assert target.privacy_status == "private"  # never accidentally public


def test_publish_target_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PublishTarget(title="x", bogus="nope")  # type: ignore[call-arg]


def test_publish_result_id_prefixed() -> None:
    result = PublishResult(
        platform="youtube", post_id="abc", url="https://x", published_via="publish:youtube"
    )
    assert result.id.startswith("pub_")


def test_fake_is_runtime_checkable_provider() -> None:
    assert isinstance(FakePublishingProvider(), PublishingProvider)


def test_fake_records_calls_and_mints_provenance() -> None:
    provider = FakePublishingProvider(platform="youtube")
    target = PublishTarget(title="My Short")
    result = asyncio.run(provider.publish(video=_video(), target=target))

    assert result.platform == "youtube"
    assert result.post_id == "fakepost1"
    assert result.url == "fake://youtube/fakepost1"
    assert result.published_via == "publish:fake"
    assert len(provider.calls) == 1
    assert provider.calls[0][1].title == "My Short"
