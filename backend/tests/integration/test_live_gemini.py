"""Live Gemini smoke test (requires a real API key; skipped otherwise).

Run only this, after setting REEL_AUTOMATION_GEMINI_API_KEY in `.env` (from the
`backend/` directory):

    python -m pytest -m integration

It exercises the real native-structured-output path: Settings -> GeminiProvider
-> live model -> responseSchema-constrained JSON -> schema validation. The
provider is constructed directly (not via the router factory, which is not yet
wired for Gemini — see ADR 0020). Skipped automatically when no key is
configured, so the default `pytest` run (and CI) never hit the network.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.llm.gemini import GeminiProvider

pytestmark = pytest.mark.integration


class _Capital(BaseModel):
    country: str
    capital: str


def test_gemini_native_structured_output() -> None:
    settings = Settings()
    if not settings.gemini_api_key.get_secret_value():
        pytest.skip("no live Gemini key configured (set REEL_AUTOMATION_GEMINI_API_KEY in .env)")

    provider = GeminiProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        base_url=settings.gemini_base_url,
    )
    out = asyncio.run(
        provider.complete_structured(
            model=settings.gemini_model,
            system="You answer concisely.",
            prompt="What is the capital of Japan?",
            schema=_Capital,
        )
    )

    assert out.country
    assert out.capital
