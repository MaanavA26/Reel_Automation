"""Tests for the real httpx fetcher's hardening (M6), via MockTransport (offline)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services.ingestion.base import FetchError
from app.services.ingestion.httpx_fetch import HttpxFetchProvider


def _public_resolver(host: str) -> list[str]:
    """Map any hostname to a single public IP so hostname checks need no DNS."""
    return ["93.184.216.34"]  # example.com's public address


def _provider(handler: Any, **kwargs: Any) -> HttpxFetchProvider:
    # follow_redirects=False: the provider follows redirects manually so its
    # SSRF/scheme guard re-runs on each hop. A public-IP resolver is injected so
    # hostname validation is hermetic (no DNS), unless a test overrides it.
    kwargs.setdefault("resolver", _public_resolver)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpxFetchProvider(client=client, **kwargs)


def test_fetches_html_with_ua_and_no_credentials() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    got = asyncio.run(_provider(handler).fetch(url="https://a.com"))
    assert got.content == b"<p>hi</p>"
    assert "ReelAutomation" in seen["ua"]
    assert seen["auth"] is None  # no credentials attached


def test_rejects_disallowed_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # image/* is not on the allowlist (HTML/text + application/pdf only).
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")

    with pytest.raises(FetchError):
        asyncio.run(_provider(handler).fetch(url="https://a.com/x.png"))


def test_allows_pdf_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

    got = asyncio.run(_provider(handler).fetch(url="https://a.com/f.pdf"))
    assert got.content == b"%PDF-1.4"
    assert got.content_type == "application/pdf"


def test_rejects_oversize_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 2000)

    with pytest.raises(FetchError):
        asyncio.run(_provider(handler, max_bytes=1000).fetch(url="https://a.com"))


def test_http_status_error_becomes_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope")

    with pytest.raises(FetchError):
        asyncio.run(_provider(handler).fetch(url="https://a.com"))


# --- SSRF guard: scheme + private/reserved IP rejection ----------------------


def test_rejects_non_http_scheme() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("request must not be sent for a disallowed scheme")

    with pytest.raises(FetchError, match="disallowed scheme"):
        asyncio.run(_provider(handler).fetch(url="file:///etc/passwd"))


def test_rejects_loopback_ip_literal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("request must not be sent to a loopback address")

    with pytest.raises(FetchError, match="blocked private/reserved"):
        asyncio.run(_provider(handler).fetch(url="http://127.0.0.1/admin"))


def test_rejects_cloud_metadata_ip_literal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("request must not be sent to the metadata endpoint")

    with pytest.raises(FetchError, match="blocked private/reserved"):
        asyncio.run(_provider(handler).fetch(url="http://169.254.169.254/latest/meta-data/"))


def test_rejects_hostname_resolving_to_private_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("request must not be sent when host resolves private")

    def private_resolver(host: str) -> list[str]:
        return ["10.0.0.5"]  # RFC1918 private

    with pytest.raises(FetchError, match="blocked private/reserved"):
        asyncio.run(
            _provider(handler, resolver=private_resolver).fetch(url="https://internal.evil.test")
        )


def test_blocks_redirect_to_loopback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        raise AssertionError("must not follow the redirect to loopback")  # pragma: no cover

    with pytest.raises(FetchError, match="blocked private/reserved"):
        asyncio.run(_provider(handler).fetch(url="https://a.com"))


def test_blocks_redirect_to_cloud_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        raise AssertionError("must not follow the redirect to metadata")  # pragma: no cover

    with pytest.raises(FetchError, match="blocked private/reserved"):
        asyncio.run(_provider(handler).fetch(url="https://a.com"))


def test_follows_public_redirect_then_fetches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://b.com/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    got = asyncio.run(_provider(handler).fetch(url="https://a.com/start"))
    assert got.content == b"<p>ok</p>"


def test_rejects_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect onward to a fresh public host.
        return httpx.Response(302, headers={"location": "https://next.com/loop"})

    with pytest.raises(FetchError, match="too many redirects"):
        asyncio.run(_provider(handler, max_redirects=2).fetch(url="https://a.com"))


# --- content-type: fail closed on a missing header ---------------------------


def test_rejects_missing_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # No content-type header at all -> must be treated as disallowed.
        return httpx.Response(200, content=b"<p>hi</p>")

    with pytest.raises(FetchError, match="disallowed content-type"):
        asyncio.run(_provider(handler).fetch(url="https://a.com"))


# --- DoS: streamed size cap + Content-Length pre-reject ----------------------


def test_pre_rejects_oversized_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "999999"},
            content=b"x" * 100,
        )

    with pytest.raises(FetchError, match="declared"):
        asyncio.run(_provider(handler, max_bytes=1000).fetch(url="https://a.com"))


def test_aborts_oversized_stream_without_declared_length() -> None:
    # A streamed body with no usable content-length pre-check must still be
    # aborted once accumulated bytes exceed the cap (the streaming guard, not
    # the pre-reject). An explicit async stream carries no content-length.
    class _Stream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            for _ in range(10):
                yield b"x" * 500

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=_Stream())

    with pytest.raises(FetchError, match="too large"):
        asyncio.run(_provider(handler, max_bytes=1000).fetch(url="https://a.com"))
