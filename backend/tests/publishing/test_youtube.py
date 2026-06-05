"""Tests for the YouTube Shorts publisher (resumable upload), fully offline.

An injected ``httpx.MockTransport`` stands in for the network. The handler
dispatches on method/URL to model the two-step resumable exchange: the initiate
``POST`` (asserts the metadata body — ``#Shorts``, privacyStatus, part query —
and returns a ``Location`` session header) then the ``PUT`` to that session URI
(asserts the raw bytes + content-length, returns the created video resource with
its ``id``). The wire contract asserted here mirrors the real YouTube Data API v3
resumable-upload protocol (verified against Google's docs). The live call is a
separate ``@pytest.mark.integration`` smoke test.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.media.schemas import RenderedVideo
from app.publishing.base import PublishTarget
from app.publishing.youtube import PublishError, YouTubeShortsPublisher

_SESSION_URI = (
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=xyz"
)
_VIDEO_BYTES = b"\x00\x01\x02fake-mp4-bytes"


def _video() -> RenderedVideo:
    return RenderedVideo(
        video_uri="file:///tmp/short.mp4",
        duration_ms=30_000,
        width=1080,
        height=1920,
        produced_via="composition:fake",
    )


def _publisher(handler: Any, *, token: str = "tok") -> YouTubeShortsPublisher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return YouTubeShortsPublisher(
        access_token=token,
        video_source=lambda _uri: _VIDEO_BYTES,
        client=client,
    )


def _publish(publisher: YouTubeShortsPublisher, *, target: PublishTarget | None = None) -> Any:
    target = target or PublishTarget(title="My Short", description="A clip", tags=["ai"])
    return asyncio.run(publisher.publish(video=_video(), target=target))


def test_full_resumable_flow() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["initiate_url"] = str(request.url).split("?")[0]
            seen["params"] = dict(request.url.params)
            seen["auth"] = request.headers.get("Authorization")
            seen["x_len"] = request.headers.get("X-Upload-Content-Length")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, headers={"Location": _SESSION_URI})
        # PUT to the session URI
        seen["put_url"] = str(request.url)
        seen["put_bytes"] = request.content
        return httpx.Response(201, json={"id": "VID123", "kind": "youtube#video"})

    result = _publish(_publisher(handler))

    # Initiate request shape.
    assert seen["initiate_url"] == "https://www.googleapis.com/upload/youtube/v3/videos"
    assert seen["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert seen["auth"] == "Bearer tok"
    assert seen["x_len"] == str(len(_VIDEO_BYTES))
    body = seen["body"]
    assert body["snippet"]["title"] == "My Short"
    assert "#Shorts" in body["snippet"]["description"]
    assert "Shorts" in body["snippet"]["tags"]
    assert body["snippet"]["categoryId"] == "22"
    assert body["status"]["privacyStatus"] == "private"

    # Upload request shape.
    assert seen["put_url"] == _SESSION_URI
    assert seen["put_bytes"] == _VIDEO_BYTES

    # Mapped result.
    assert result.platform == "youtube"
    assert result.post_id == "VID123"
    assert result.url == "https://www.youtube.com/watch?v=VID123"
    assert result.published_via == "publish:youtube"


def test_shorts_tag_not_duplicated_when_present() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, headers={"Location": _SESSION_URI})
        return httpx.Response(201, json={"id": "VID"})

    _publish(
        _publisher(handler),
        target=PublishTarget(title="t", description="already a #shorts clip"),
    )
    # Case-insensitive: not appended a second time.
    assert captured["body"]["snippet"]["description"].lower().count("#shorts") == 1


def test_missing_location_header_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # initiate returns 200 but no Location -> contract violation
        return httpx.Response(200)

    with pytest.raises(PublishError, match="Location"):
        _publish(_publisher(handler))


def test_missing_video_id_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": _SESSION_URI})
        return httpx.Response(201, json={"kind": "youtube#video"})  # no id

    with pytest.raises(PublishError, match="id"):
        _publish(_publisher(handler))


def test_transport_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(httpx.HTTPStatusError):
        _publish(_publisher(handler))


def test_upload_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": _SESSION_URI})
        return httpx.Response(403, json={"error": "quota"})  # PUT fails

    with pytest.raises(httpx.HTTPStatusError):
        _publish(_publisher(handler))


def test_missing_token_raises() -> None:
    with pytest.raises(PublishError):
        YouTubeShortsPublisher(access_token="", video_source=lambda _u: b"")


def test_token_not_leaked_in_repr() -> None:
    publisher = YouTubeShortsPublisher(
        access_token="super-secret-token", video_source=lambda _u: b""
    )
    assert "super-secret-token" not in repr(publisher)
