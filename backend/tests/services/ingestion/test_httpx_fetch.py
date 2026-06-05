"""Tests for the real httpx fetcher's hardening (M6), via MockTransport (offline)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services.ingestion.base import FetchError
from app.services.ingestion.httpx_fetch import HttpxFetchProvider


def _provider(handler: Any, **kwargs: Any) -> HttpxFetchProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
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
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(FetchError):
        asyncio.run(_provider(handler).fetch(url="https://a.com/f.pdf"))


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
