"""Live stock-visual retrieval smoke test (requires a real API key; skipped otherwise).

Run only this, after exporting a Pexels API key (from the `backend/` directory):

    REEL_AUTOMATION_STOCK_API_KEY=... python -m pytest -m integration

It exercises the real path: ``StockVisualProvider`` -> live Pexels Videos API ->
`VisualClip`s carrying real (provider-minted) asset URIs. The key is read from
the environment directly (not `Settings`) because this seam takes its key at
construction — keeping it config-root-agnostic. It is skipped automatically when
no key is configured, so the default `pytest` run (and CI) never hit the network.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.media.visuals.stock import StockVisualProvider

pytestmark = pytest.mark.integration

_KEY_ENV = "REEL_AUTOMATION_STOCK_API_KEY"


def test_stock_visuals_against_live_api() -> None:
    key = os.environ.get(_KEY_ENV)
    if not key:
        pytest.skip(f"no live stock-visual key configured (set {_KEY_ENV})")

    provider = StockVisualProvider(api_key=key)
    clips = asyncio.run(provider.search(query="city skyline timelapse", limit=5))

    assert clips, "live stock-visual search returned no results"
    assert all(c.uri.startswith("http") for c in clips)
    assert all(c.width > 0 and c.height > 0 for c in clips)
