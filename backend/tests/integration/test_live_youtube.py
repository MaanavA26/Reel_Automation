"""Live YouTube transcript smoke test for the real provider (M-LP).

Needs outbound network **and** the optional ``youtube`` extra
(``pip install '.[youtube]'``); run with ``python -m pytest -m integration``.
Skipped by default (and when the dep/network is unavailable) so the normal
suite/CI stays hermetic. See ADR 0015.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.ingestion.transcript import normalize_transcript
from app.services.ingestion.youtube_transcript_provider import YouTubeTranscriptProvider

pytestmark = pytest.mark.integration

# A stable, captioned reference video ("Me at the zoo", the first YouTube upload).
_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_fetch_and_normalize_live_transcript() -> None:
    pytest.importorskip("youtube_transcript_api", reason="optional 'youtube' extra not installed")
    try:
        segments = asyncio.run(YouTubeTranscriptProvider().fetch(url=_URL))
    except Exception as exc:  # network/transcript unavailable in this environment
        pytest.skip(f"youtube transcript unavailable: {exc}")

    assert segments
    text = normalize_transcript(segments)
    assert text.strip()
