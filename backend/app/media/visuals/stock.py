"""Live `VisualProvider` adapter over a stock-media REST API (httpx-based).

The first concrete `VisualProvider`, retrieving short B-roll clips for a query
so the composition step has real assets to lay under the narration. It speaks
the Pexels Videos API — the canonical free vertical-B-roll source — as the one
documented wire contract behind the provider-neutral protocol (each adapter
speaks its own API, the way the Tavily and Brave search adapters each do; ADR
0013/0021). Pexels' ``GET /videos/search`` authenticates with an
``Authorization`` header and returns ranked videos whose ``video_files[]`` carry
the playable file URLs that map onto `VisualClip.uri`.

Per CLAUDE.md §3.3/§4 this is a deterministic *tool*: the content strategist
decides *what* the B-roll should depict; this adapter *executes* the retrieval
and is the only thing that mints a real asset ``uri`` (an LLM never authors one),
keeping the evidence/retrieval-vs-inference boundary structural (CLAUDE.md §11).

Built on ``httpx`` (already a runtime dependency) so request building and the
response → `VisualClip` mapping are unit-testable **offline** via
``httpx.MockTransport``; only the live call needs network (a
``@pytest.mark.integration`` smoke test). Hardened like the Brave adapter
(ADR 0021): a bounded timeout, an injectable client, and a key that never leaks
into logs/reprs/errors. The key is taken at construction (not from `Settings`),
keeping this seam config-root-agnostic — the integration test reads
``REEL_AUTOMATION_STOCK_API_KEY`` from the environment directly.

Error boundary (mirrors ADR 0013/0021): *operational* failures (429 rate-limit,
timeout, 5xx via ``raise_for_status``) propagate as ``httpx`` errors for the
caller/Orchestrator to handle (retries/budgets live in that layer) — they are
not swallowed. Only an unparseable response *shape* is wrapped in `VisualError`;
a response with no videos is a valid empty result, not an error.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.media.visuals.base import VisualClip, VisualKind

PROVIDER_NAME = "stock"
_DEFAULT_BASE_URL = "https://api.pexels.com"
# Pexels caps ``per_page`` at 80; the protocol's ``limit`` is caller-controlled,
# so it must be clamped before it reaches the wire.
_MAX_PER_PAGE = 80
# Bound the upstream-body excerpt in error messages so a full provider response
# never leaks into logs / surfaced errors (info-leak guard, ADR 0043).
_ERR_BODY_MAX = 500


class VisualError(RuntimeError):
    """Raised when a response cannot be parsed into `VisualClip`s.

    Transport/status failures (timeout, 429, 5xx) are *not* wrapped — they
    propagate as ``httpx`` errors for the caller to handle (ADR 0007/0021).
    Defined locally (mirroring the search adapters' `SearchError`) so this band
    owns its own error type and stays self-contained.
    """


class StockVisualProvider:
    """A `VisualProvider` over the Pexels Videos API.

    Hardened like the Brave adapter: a bounded timeout and a key that never leaks
    into logs, reprs, or error messages. SSRF caps (size/redirect/content-type)
    are intentionally omitted — like the search adapters this calls a single
    trusted API endpoint, not attacker-influenced URLs.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise VisualError("api_key is required (set REEL_AUTOMATION_STOCK_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def search(self, *, query: str, limit: int = 10) -> list[VisualClip]:
        """Run ``query`` against Pexels and map hits to `VisualClip`s.

        Returns at most ``limit`` clips, mirroring `FakeVisualProvider`. A video
        with no usable file ``link`` is skipped (it cannot become a B-roll uri).
        Requests portrait orientation — the system targets vertical short-form.
        """
        response = await self._client.get(
            f"{self._base_url}/videos/search",
            headers={"Authorization": self._api_key},
            params={
                "query": query,
                "per_page": min(max(limit, 1), _MAX_PER_PAGE),
                "orientation": "portrait",
            },
        )
        response.raise_for_status()
        data: Any = response.json()
        return _map_videos(data, limit=limit)


def _map_videos(data: Any, *, limit: int) -> list[VisualClip]:
    """Map a Pexels ``/videos/search`` payload to `VisualClip`s.

    Pexels nests hits under ``videos[]``; each video has ``video_files[]`` (the
    playable renditions), ``width``/``height``, an integer ``duration`` in
    seconds, and a ``user.name`` credit. An absent/empty ``videos`` list means
    "no results" — a valid empty outcome, not an error. Only a *present but
    mistyped* payload is wrapped in `VisualError`.
    """
    if not isinstance(data, dict):
        raise VisualError(f"unexpected stock response shape: {repr(data)[:_ERR_BODY_MAX]}")

    raw_videos = data.get("videos")
    if raw_videos is None:
        return []
    if not isinstance(raw_videos, list):
        raise VisualError(f"unexpected stock 'videos' shape: {repr(raw_videos)[:_ERR_BODY_MAX]}")

    clips: list[VisualClip] = []
    for item in raw_videos:
        clip = _map_video(item)
        if clip is not None:
            clips.append(clip)
    return clips[:limit]


def _map_video(item: Any) -> VisualClip | None:
    """Map one Pexels video object to a `VisualClip`, or ``None`` to skip it.

    Skips a video lacking a usable file ``link`` (it cannot become a B-roll uri).
    Dimensions and duration fall back to the file rendition when the top-level
    fields are absent/mistyped; ``duration`` (seconds) is converted to integer
    milliseconds to match the media layer's ms convention (ADR 0019).
    """
    if not isinstance(item, dict):
        return None

    file = _pick_video_file(item.get("video_files"))
    if file is None:
        return None
    link = file.get("link")
    if not link:
        return None

    width = _coerce_dim(item.get("width")) or _coerce_dim(file.get("width"))
    height = _coerce_dim(item.get("height")) or _coerce_dim(file.get("height"))
    if width is None or height is None:
        return None  # a clip with no dimensions cannot be composited

    duration_s = item.get("duration")
    duration_ms = (
        int(duration_s) * 1000 if isinstance(duration_s, (int, float)) and duration_s >= 0 else None
    )

    user = item.get("user")
    attribution = user.get("name") if isinstance(user, dict) else None

    return VisualClip(
        uri=str(link),
        kind=VisualKind.VIDEO,
        width=width,
        height=height,
        duration_ms=duration_ms,
        attribution=attribution if isinstance(attribution, str) else None,
        produced_via=f"visuals:{PROVIDER_NAME}",
    )


def _pick_video_file(video_files: Any) -> dict[str, Any] | None:
    """Return the first well-formed rendition from ``video_files[]``, else None."""
    if not isinstance(video_files, list):
        return None
    for file in video_files:
        if isinstance(file, dict) and file.get("link"):
            return file
    return None


def _coerce_dim(value: Any) -> int | None:
    """Coerce a positive integer dimension, else None (a `VisualClip` needs >0)."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
