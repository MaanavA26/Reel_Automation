"""Live trends-source smoke test (requires a real API key; skipped otherwise).

Run only this, after exporting a key + base URL for a real trends/keyword API
(from the `backend/` directory):

    REEL_AUTOMATION_TRENDS_API_KEY=... REEL_AUTOMATION_TRENDS_BASE_URL=... \
        python -m pytest -m integration

The key is read directly from the environment (not from `Settings`) because the
topics package is a standalone §3.4 layer and does not own a config field. The
test is skipped automatically when no key is configured, so the default `pytest`
run (and CI) never hit the network.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.topics.live import HttpTrendProvider

pytestmark = pytest.mark.integration


def test_trends_against_live_api() -> None:
    key = os.environ.get("REEL_AUTOMATION_TRENDS_API_KEY", "")
    if not key:
        pytest.skip("no live trends key configured (set REEL_AUTOMATION_TRENDS_API_KEY)")

    base_url = os.environ.get("REEL_AUTOMATION_TRENDS_BASE_URL")
    kwargs = {"base_url": base_url} if base_url else {}
    provider = HttpTrendProvider(api_key=key, **kwargs)
    ideas = asyncio.run(provider.discover(niche="artificial intelligence", limit=5))

    assert ideas, "live trends source returned no ideas"
    assert all(i.title for i in ideas)
