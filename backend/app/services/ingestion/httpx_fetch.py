"""Real HTTP `FetchProvider` (httpx).

This is the engine's first fetch of attacker-influenceable URLs (search-sourced),
so the request is hardened: an ``http``/``https``-only scheme allowlist, an
SSRF guard that rejects hosts resolving to a private/loopback/link-local/reserved
IP (checked **before the request and on every redirect hop**), a bounded timeout,
a streamed response-size cap (aborted mid-stream, with a ``Content-Length``
pre-reject), a content-type allowlist (HTML/text + PDF, **fail-closed** on a
missing header), capped redirects, and **no credentials/cookies**.

Redirects are followed *manually* (``follow_redirects=False`` per request) so the
SSRF + scheme checks re-run on every hop — a server cannot redirect us from a
benign public URL to ``http://169.254.169.254/`` or ``http://127.0.0.1/``.

Unit-tested offline via ``httpx.MockTransport`` (with an injected resolver so
hostname checks need no DNS); a live smoke test is ``@pytest.mark.integration``.
See ADR 0008 (HTML), ADR 0014 (PDF), and ADR 0043 (fetch/render hardening).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

import httpx

from app.services.ingestion.base import FetchedContent, FetchError

_DEFAULT_UA = "ReelAutomationBot/0.1 (+research ingestion)"
_MAX_BYTES = 5_000_000  # 5 MB cap
# HTML/text for WEB sources; application/pdf for PDF sources (ADR 0014). The size
# cap and no-credentials posture still apply to PDF fetches.
_ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/pdf")
_ALLOWED_SCHEMES = ("http", "https")

#: Resolves a hostname to a list of textual IP addresses (the DNS seam).
Resolver = Callable[[str], list[str]]


def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its IP addresses via the stdlib (the production seam)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if ``ip`` is in a range we must never fetch from (SSRF guard).

    Covers loopback, RFC1918/ULA private, link-local (incl. the cloud-metadata
    endpoint at ``169.254.169.254``), reserved, multicast, and the unspecified
    address. IPv4-mapped IPv6 (``::ffff:127.0.0.1``) is unwrapped first.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


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
        resolver: Resolver | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._resolver = resolver or _default_resolver
        # Redirects are handled manually (per-request follow_redirects=False) so
        # the SSRF/scheme guard re-runs on every hop; the client default is moot.
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            # No auth/cookies are ever attached.
        )

    def _validate_url(self, url: httpx.URL) -> None:
        """Reject a disallowed scheme or a host resolving to a blocked IP.

        Raises `FetchError` before any request reaches the network — applied to
        the initial URL and re-applied to every redirect target.
        """
        scheme = url.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise FetchError(f"disallowed scheme {scheme!r} for {str(url)!r}")

        host = url.host
        if not host:
            raise FetchError(f"missing host in url {str(url)!r}")

        try:
            literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(
                host
            )
        except ValueError:
            literal = None

        if literal is not None:
            candidates = [literal]
        else:
            try:
                resolved = self._resolver(host)
            except OSError as exc:
                raise FetchError(f"could not resolve host {host!r}: {exc}") from exc
            try:
                candidates = [ipaddress.ip_address(addr) for addr in resolved]
            except ValueError as exc:
                raise FetchError(f"unparseable address for host {host!r}: {exc}") from exc

        if not candidates:
            raise FetchError(f"host {host!r} resolved to no addresses")
        for ip in candidates:
            if _is_blocked_ip(ip):
                raise FetchError(f"blocked private/reserved address {str(ip)!r} for host {host!r}")

    async def fetch(self, *, url: str) -> FetchedContent:
        current = httpx.URL(url)
        headers = {"User-Agent": self._user_agent}

        for _ in range(self._max_redirects + 1):
            self._validate_url(current)
            try:
                async with self._client.stream(
                    "GET", current, headers=headers, follow_redirects=False
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(f"redirect without Location for {str(current)!r}")
                        current = current.join(location)
                        continue
                    # Return the originally-requested url for provenance
                    # continuity (the prior contract); errors key off the hop.
                    return await self._read_capped(response, returned_url=url, hop=str(current))
            except httpx.HTTPError as exc:
                raise FetchError(
                    f"fetch failed for {str(current)!r}: {type(exc).__name__}: {exc}"
                ) from exc

        raise FetchError(f"too many redirects (> {self._max_redirects}) for {url!r}")

    async def _read_capped(
        self, response: httpx.Response, *, returned_url: str, hop: str
    ) -> FetchedContent:
        """Validate status/content-type then stream the body under the size cap.

        ``returned_url`` is carried into `FetchedContent` (the originally-requested
        url, preserving the prior provenance contract); ``hop`` is the actual
        fetched url and is used only in diagnostic messages.
        """
        response.raise_for_status()

        content_type = response.headers.get("content-type")
        # Fail closed: a missing content-type is treated as disallowed.
        if content_type is None or not any(t in content_type.lower() for t in _ALLOWED_TYPES):
            raise FetchError(f"disallowed content-type {content_type!r} for {hop!r}")

        # Pre-reject when the server already declares an oversized body.
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                declared_len = int(declared)
            except ValueError:
                declared_len = -1  # malformed header — fall through to the streamed cap
            if declared_len > self._max_bytes:
                raise FetchError(f"response too large ({declared} bytes declared) for {hop!r}")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise FetchError(f"response too large (> {self._max_bytes} bytes) for {hop!r}")
            chunks.append(chunk)

        return FetchedContent(url=returned_url, content=b"".join(chunks), content_type=content_type)
