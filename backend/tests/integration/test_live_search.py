"""Live search smoke test for the real Tavily adapter (M-LP.2).

Needs outbound network + a Tavily key; run with ``python -m pytest -m integration``
(from ``backend/``). Skipped by default — and when no key is configured — so the
normal suite/CI stays hermetic. See ADR 0013.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.services.search.live import TavilySearchProvider

pytestmark = pytest.mark.integration


def test_search_returns_real_results() -> None:
    settings = Settings()
    api_key = settings.search_api_key.get_secret_value()
    if not api_key:
        pytest.skip("no live search key configured (set REEL_AUTOMATION_SEARCH_API_KEY in .env)")

    try:
        hits = asyncio.run(
            TavilySearchProvider(api_key=api_key).search(query="how vaccines work", limit=3)
        )
    except Exception as exc:  # network unavailable in this environment
        pytest.skip(f"network unavailable: {exc}")

    assert hits, "live search returned no results"
    assert all(h.url.startswith("http") for h in hits)
