"""Real YouTube `TranscriptProvider` backed by ``youtube-transcript-api`` (1.x).

A lightweight, credential-free adapter: it scrapes YouTube's public timedtext
endpoint via the third-party ``youtube-transcript-api`` library. That library is
an **optional** dependency (``pip install '.[youtube]'``) — the sandbox cannot
reach PyPI, so it is imported **lazily** inside ``fetch`` rather than at module
top. This keeps the package importable (and all hermetic tests green) without
the dep installed; the live path is exercised by a ``@pytest.mark.integration``
smoke test. The underlying library is synchronous, so the blocking call runs in
a worker thread to honor the async `TranscriptProvider` contract. See ADR 0015.
"""

from __future__ import annotations

import asyncio

from app.services.ingestion.transcript import (
    TranscriptError,
    TranscriptSegment,
    extract_video_id,
)


class YouTubeTranscriptProvider:
    """Fetches caption segments for a YouTube URL via ``youtube-transcript-api``.

    ``languages`` is the preference order passed through to the library (default
    English); the library falls back to any available track when none match.
    """

    name = "youtube-transcript-api"

    def __init__(self, *, languages: tuple[str, ...] = ("en",)) -> None:
        self._languages = languages

    async def fetch(self, *, url: str) -> list[TranscriptSegment]:
        video_id = extract_video_id(url)
        return await asyncio.to_thread(self._fetch_sync, video_id)

    def _fetch_sync(self, video_id: str) -> list[TranscriptSegment]:
        """Blocking fetch; wraps every library failure into `TranscriptError`."""
        try:
            from youtube_transcript_api import (
                YouTubeTranscriptApi,
                YouTubeTranscriptApiException,
            )
        except ModuleNotFoundError as exc:  # optional dep not installed
            raise TranscriptError(
                "youtube-transcript-api is not installed; "
                "install the optional 'youtube' extra to enable YouTube ingestion"
            ) from exc

        try:
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=list(self._languages))
        except YouTubeTranscriptApiException as exc:
            # Covers transcripts-disabled, none-found, video-unavailable,
            # age-restricted, IP/request-blocked — all skip-and-continue cases.
            raise TranscriptError(
                f"could not retrieve transcript for video {video_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

        return [
            TranscriptSegment(text=snippet.text, start=snippet.start, duration=snippet.duration)
            for snippet in fetched
        ]
