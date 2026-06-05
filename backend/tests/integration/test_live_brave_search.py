"""Live Brave Search smoke test (requires a real API key; skipped otherwise).

Run only this, after setting ``REEL_AUTOMATION_BRAVE_API_KEY`` in `.env` (from
the `backend/` directory):

    python -m pytest -m integration

It exercises the real path: Settings -> BraveSearchProvider -> live Brave Web
Search API -> `SearchResult`s carrying real (provider-minted) URLs. It is
skipped automatically when no key is configured, so the default `pytest` run
(and CI) never hit the network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.services.search.brave_search import BraveSearchProvider

pytestmark = pytest.mark.integration


def test_brave_search_against_live_api() -> None:
    settings = Settings()
    key = settings.brave_api_key.get_secret_value()
    if not key:
        pytest.skip("no live Brave key configured (set REEL_AUTOMATION_BRAVE_API_KEY in .env)")

    provider = BraveSearchProvider(api_key=key)
    results = asyncio.run(provider.search(query="how vaccines work", limit=5))

    assert results, "live Brave search returned no results"
    assert all(r.url.startswith("http") for r in results)
