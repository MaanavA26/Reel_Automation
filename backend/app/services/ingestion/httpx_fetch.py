"""Real HTTP `FetchProvider` (httpx).

This is the engine's first fetch of attacker-influenceable URLs (search-sourced),
so the request is hardened: bounded timeout, capped response size, capped
redirects, a content-type allowlist (HTML/text + PDF), and **no credentials/cookies**.
Unit-tested offline via ``httpx.MockTransport``; a live smoke test is
``@pytest.mark.integration``. See ADR 0008 (HTML) and ADR 0014 (PDF).
"""

from __future__ import annotations

import httpx

from app.services.ingestion.base import FetchedContent, FetchError

_DEFAULT_UA = "ReelAutomationBot/0.1 (+research ingestion)"
_MAX_BYTES = 5_000_000  # 5 MB cap
# HTML/text for WEB sources; application/pdf for PDF sources (ADR 0014). The size
# cap and no-credentials posture still apply to PDF fetches.
_ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/pdf")


class HttpxFetchProvider:
    """Fetches URLs over HTTP with hardened defaults."""

    name = "httpx"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        max_redirects: int = 5,
        max_bytes: int = _MAX_BYTES,
        user_agent: str = _DEFAULT_UA,
    ) -> None:
        self._max_bytes = max_bytes
        self._user_agent = user_agent
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
            # No auth/cookies are ever attached.
        )

    async def fetch(self, *, url: str) -> FetchedContent:
        try:
            response = await self._client.get(url, headers={"User-Agent": self._user_agent})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetchError(f"fetch failed for {url!r}: {type(exc).__name__}: {exc}") from exc

        content_type = response.headers.get("content-type")
        if content_type and not any(t in content_type.lower() for t in _ALLOWED_TYPES):
            raise FetchError(f"disallowed content-type {content_type!r} for {url!r}")

        content = response.content
        if len(content) > self._max_bytes:
            raise FetchError(f"response too large ({len(content)} bytes) for {url!r}")

        return FetchedContent(url=url, content=content, content_type=content_type)
