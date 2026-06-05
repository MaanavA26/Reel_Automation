"""Live `PublishingProvider` adapter over the YouTube Data API v3 (httpx-based).

Uploads a finished `RenderedVideo` as a **YouTube Short** via the API's
*resumable upload* protocol — a two-step exchange:

1. **Initiate** ``POST {UPLOAD_BASE}/youtube/v3/videos?uploadType=resumable&part=snippet,status``
   with the video-resource metadata JSON in the body and ``X-Upload-Content-Type`` /
   ``X-Upload-Content-Length`` headers describing the file to come. The response
   carries the **resumable session URI in the ``Location`` header**.
2. **Upload** ``PUT {session_uri}`` with the raw video bytes as the body. The
   success response (``200``/``201``) is the created *video resource*; the new
   video's id is its top-level ``id`` field.

Built on ``httpx`` (already a runtime dependency) so request building and the
response → `PublishResult` mapping are unit-testable **offline** via
``httpx.MockTransport`` (the two-request exchange is dispatched on method/URL);
only the live call needs network — a ``@pytest.mark.integration`` smoke test.

Hardened like the Brave / TTS adapters (ADR 0021 / 0022): a bounded timeout, the
OAuth access token passed in **at construction** (never read from global
``Settings``, so this file stays out of ``config.py`` — see ADR 0033), and
``raise_for_status`` on transport errors.

Storage seam (mirrors the TTS `AudioSink`, inverted for *reading*): a
`RenderedVideo` is a storage-owned descriptor (ADR 0019), so this adapter does
not choose where the bytes live — a `VideoSource` callable is injected at
construction that resolves a ``video_uri`` to its bytes. Tests inject an in-memory
source; a real deployment injects an object-store / filesystem reader.

Shorts signalling: YouTube classifies a vertical, short video as a Short by its
format; the documented creator-side convention is the ``#Shorts`` tag in the
title/description, which this adapter appends to the description.

Error boundary (mirrors ADR 0013/0021/0022): operational failures (401/403/429,
timeouts, 5xx) propagate as native ``httpx`` errors via ``raise_for_status`` for
the orchestrator to handle (retries/budgets live there). Only a contract-violating
response *shape* — a missing ``Location`` on initiate, or a missing ``id`` on
completion — is wrapped in `PublishError`. The token never leaks into logs,
reprs, or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.core.lifecycle import CloseOwnedClientMixin
from app.media.schemas import RenderedVideo
from app.publishing.base import PublishResult, PublishTarget

PLATFORM = "youtube"
PROVIDER_NAME = "youtube"

# Bound the upstream-body excerpt in error messages so a full provider response
# never lands in logs / surfaced errors (info-leak guard, ADR 0043/0044).
_ERR_BODY_MAX = 500

_DEFAULT_UPLOAD_BASE = "https://www.googleapis.com/upload"
# part values to set + echo back; snippet carries title/description/tags,
# status carries privacyStatus.
_PARTS = "snippet,status"
# Category 22 = "People & Blogs", the safe default for faceless short-form;
# categoryId is required by the API for an `insert`.
_DEFAULT_CATEGORY_ID = "22"
_SHORTS_TAG = "#Shorts"

#: Resolves a `RenderedVideo.video_uri` to the raw video bytes to upload.
#: Injected at construction so the adapter stays storage-neutral (ADR 0019) —
#: the read-side analogue of the TTS adapter's `AudioSink`.
VideoSource = Callable[[str], bytes]


class PublishError(RuntimeError):
    """Raised on a malformed / contract-violating publish response.

    Transport-level failures surface as their native ``httpx`` exceptions
    (e.g. ``httpx.HTTPStatusError`` from ``raise_for_status``); this class
    covers response *shape* failures specific to the resumable-upload contract
    (missing session ``Location``, missing video ``id``) and is also raised by
    the not-yet-implemented sibling adapters (``"adapter pending"``).
    """


class YouTubeShortsPublisher(CloseOwnedClientMixin):
    """A `PublishingProvider` that uploads a `RenderedVideo` as a YouTube Short.

    The OAuth access token is supplied at construction (never from ``Settings``);
    token *refresh* (exchanging a long-lived refresh token for a fresh access
    token) is the caller's responsibility and a documented deferral (ADR 0033) —
    this adapter assumes a currently-valid access token.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_token: str,
        video_source: VideoSource,
        upload_base: str = _DEFAULT_UPLOAD_BASE,
        category_id: str = _DEFAULT_CATEGORY_ID,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        if not access_token:
            raise PublishError("access_token is required (OAuth bearer token)")
        self._access_token = access_token
        self._video_source = video_source
        self._upload_base = upload_base.rstrip("/")
        self._category_id = category_id
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def publish(self, *, video: RenderedVideo, target: PublishTarget) -> PublishResult:
        """Upload ``video`` with ``target`` metadata via resumable upload.

        Resolves the video bytes through the injected `VideoSource`, initiates a
        resumable session (reading the ``Location`` header), PUTs the bytes, and
        maps the returned video resource to a `PublishResult`.
        """
        data = self._video_source(video.video_uri)
        metadata = _build_metadata(target, category_id=self._category_id)

        session_uri = await self._initiate(metadata=metadata, content_length=len(data))
        return await self._upload(session_uri=session_uri, data=data)

    async def _initiate(self, *, metadata: dict[str, Any], content_length: int) -> str:
        """Step 1 — open a resumable session; return its ``Location`` URI."""
        response = await self._client.post(
            f"{self._upload_base}/youtube/v3/videos",
            params={"uploadType": "resumable", "part": _PARTS},
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(content_length),
            },
            json=metadata,
        )
        response.raise_for_status()
        session_uri = response.headers.get("Location")
        if not session_uri:
            raise PublishError(
                "resumable upload initiate response missing 'Location' session header"
            )
        return session_uri

    async def _upload(self, *, session_uri: str, data: bytes) -> PublishResult:
        """Step 2 — PUT the bytes to the session; map the video resource."""
        response = await self._client.put(
            session_uri,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "video/*",
            },
            content=data,
        )
        response.raise_for_status()
        return _map_result(response.json())


def _build_metadata(target: PublishTarget, *, category_id: str) -> dict[str, Any]:
    """Build the YouTube video-resource JSON from a `PublishTarget`.

    Appends the ``#Shorts`` tag to the description (the documented Shorts-signalling
    convention) unless already present, and adds it to the tag list.
    """
    description = target.description
    if _SHORTS_TAG.lower() not in description.lower():
        description = f"{description}\n\n{_SHORTS_TAG}".strip()

    tags = list(target.tags)
    if not any(t.lower() == "shorts" for t in tags):
        tags.append("Shorts")

    return {
        "snippet": {
            "title": target.title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": target.privacy_status},
    }


def _map_result(data: Any) -> PublishResult:
    """Map a returned YouTube video resource to a `PublishResult`.

    The new video's id is the resource's top-level ``id``. The watch url is the
    canonical ``watch?v=`` form (also reachable at ``/shorts/{id}``). A missing
    or mistyped ``id`` is a contract violation wrapped in `PublishError`.
    """
    if not isinstance(data, dict):
        raise PublishError(f"unexpected YouTube response shape: {repr(data)[:_ERR_BODY_MAX]}")
    video_id = data.get("id")
    if not video_id or not isinstance(video_id, str):
        raise PublishError(f"YouTube response missing video 'id': {repr(data)[:_ERR_BODY_MAX]}")
    return PublishResult(
        platform=PLATFORM,
        post_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_via=f"publish:{PROVIDER_NAME}",
    )
