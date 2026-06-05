"""Tests for the IngestionService YouTube transcript path (M-LP, offline)."""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.research_state import Source, SourceType
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fake_transcript import FakeTranscriptProvider
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionError, IngestionService
from app.services.ingestion.transcript import TranscriptSegment

_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _youtube(url: str = _URL) -> Source:
    return Source(url=url, type=SourceType.YOUTUBE, discovered_via="search:fake")


def _web(url: str) -> Source:
    return Source(url=url, type=SourceType.WEB, discovered_via="search:fake")


def _html(url: str, body: str) -> FetchedContent:
    return FetchedContent(url=url, content=f"<p>{body}</p>".encode(), content_type="text/html")


def test_youtube_source_is_transcribed_to_chunks() -> None:
    yt = _youtube()
    transcript = FakeTranscriptProvider(
        {_URL: [TranscriptSegment(text="hello"), TranscriptSegment(text="world")]}
    )
    service = IngestionService(FakeFetchProvider(), transcript_provider=transcript)
    chunks = asyncio.run(service.ingest([yt]))
    assert chunks and all(c.source_id == yt.id for c in chunks)
    assert "hello world" in " ".join(c.text for c in chunks)


def test_youtube_skipped_when_no_transcript_provider() -> None:
    yt, web = _youtube(), _web("https://a.com")
    fetch = FakeFetchProvider({"https://a.com": _html("https://a.com", "fallback")})
    # No transcript_provider injected → YouTube source falls through to skip, web still ingested.
    chunks = asyncio.run(IngestionService(fetch).ingest([yt, web]))
    assert all(c.source_id == web.id for c in chunks)


def test_youtube_failure_is_skipped_but_others_continue() -> None:
    yt, web = _youtube(), _web("https://a.com")
    fetch = FakeFetchProvider({"https://a.com": _html("https://a.com", "ok")})
    transcript = FakeTranscriptProvider()  # unmapped URL → TranscriptError → skip
    service = IngestionService(fetch, transcript_provider=transcript)
    chunks = asyncio.run(service.ingest([yt, web]))
    assert chunks and all(c.source_id == web.id for c in chunks)
    assert [c.url for c in transcript.calls] == [_URL]  # provider was tried


def test_empty_transcript_yields_no_chunks() -> None:
    yt = _youtube()
    transcript = FakeTranscriptProvider({_URL: []})  # empty → normalized to "" → no chunks
    service = IngestionService(FakeFetchProvider(), transcript_provider=transcript)
    with pytest.raises(IngestionError):
        asyncio.run(service.ingest([yt]))


def test_mixed_web_and_youtube_both_ingested() -> None:
    yt, web = _youtube(), _web("https://a.com")
    fetch = FakeFetchProvider({"https://a.com": _html("https://a.com", "article")})
    transcript = FakeTranscriptProvider({_URL: [TranscriptSegment(text="spoken")]})
    service = IngestionService(fetch, transcript_provider=transcript)
    chunks = asyncio.run(service.ingest([web, yt]))
    assert {c.source_id for c in chunks} == {web.id, yt.id}
    joined = " ".join(c.text for c in chunks)
    assert "article" in joined and "spoken" in joined
