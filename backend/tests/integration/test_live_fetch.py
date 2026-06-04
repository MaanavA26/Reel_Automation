"""Live fetch smoke test for the real httpx fetcher (M6).

Needs outbound network; run with ``python -m pytest -m integration``. Skipped by
default so the normal suite/CI stays hermetic.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.ingestion.httpx_fetch import HttpxFetchProvider
from app.services.ingestion.parser import parse_html

pytestmark = pytest.mark.integration


def test_fetch_and_parse_example_com() -> None:
    try:
        fetched = asyncio.run(HttpxFetchProvider().fetch(url="https://example.com"))
    except Exception as exc:  # network unavailable in this environment
        pytest.skip(f"network unavailable: {exc}")

    assert fetched.content
    text = parse_html(fetched.content, fetched.content_type)
    assert "example" in text.lower()
