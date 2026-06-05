"""Tests for the pure transcript helpers + the fake provider (M-LP, offline)."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ingestion.fake_transcript import FakeTranscriptProvider
from app.services.ingestion.transcript import (
    TranscriptError,
    TranscriptProvider,
    TranscriptSegment,
    extract_video_id,
    normalize_transcript,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_handles_common_url_shapes(url: str) -> None:
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["https://example.com", "https://youtube.com/", "not a url"])
def test_extract_video_id_raises_on_no_id(url: str) -> None:
    with pytest.raises(TranscriptError):
        extract_video_id(url)


def test_normalize_transcript_joins_and_collapses_whitespace() -> None:
    segments = [
        TranscriptSegment(text="  hello\nthere ", start=0.0, duration=1.0),
        TranscriptSegment(text="", start=1.0, duration=0.5),  # dropped
        TranscriptSegment(text="general\tkenobi", start=1.5, duration=1.0),
    ]
    assert normalize_transcript(segments) == "hello there general kenobi"


def test_normalize_transcript_empty_is_empty_string() -> None:
    assert normalize_transcript([]) == ""


def test_fake_provider_is_a_transcript_provider() -> None:
    assert isinstance(FakeTranscriptProvider(), TranscriptProvider)


def test_fake_provider_replays_and_records() -> None:
    segs = [TranscriptSegment(text="alpha")]
    provider = FakeTranscriptProvider({"https://youtu.be/aaaaaaaaaaa": segs})
    got = asyncio.run(provider.fetch(url="https://youtu.be/aaaaaaaaaaa"))
    assert got == segs
    assert [c.url for c in provider.calls] == ["https://youtu.be/aaaaaaaaaaa"]


def test_fake_provider_unmapped_url_raises() -> None:
    with pytest.raises(TranscriptError):
        asyncio.run(FakeTranscriptProvider().fetch(url="https://youtu.be/missing00000"))
