"""Tests for the in-memory FakeFetchProvider (M6)."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ingestion.base import FetchedContent, FetchError
from app.services.ingestion.fakes import FakeFetchProvider


def test_returns_scripted_content_and_records_calls() -> None:
    provider = FakeFetchProvider(
        {
            "https://a.com": FetchedContent(
                url="https://a.com", content=b"<p>hi</p>", content_type="text/html"
            )
        }
    )
    got = asyncio.run(provider.fetch(url="https://a.com"))
    assert got.content == b"<p>hi</p>"
    assert provider.calls[0].url == "https://a.com"


def test_unmapped_url_raises_fetch_error() -> None:
    provider = FakeFetchProvider({})
    with pytest.raises(FetchError):
        asyncio.run(provider.fetch(url="https://missing.com"))
