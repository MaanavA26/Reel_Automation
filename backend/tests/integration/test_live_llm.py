"""Live LLM smoke test (requires a real API key; skipped otherwise).

Run only this, after filling in `.env` (from the `backend/` directory):

    python -m pytest -m integration

It exercises the full real path: Settings -> router -> OpenAICompatibleProvider
-> live model -> schema validation -> ResearchPlan. It is skipped automatically
when no key is configured, so the default `pytest` run (and CI) never hit the
network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.research_planner import ResearchPlannerAgent
from app.core.config import Settings
from app.services.llm.factory import build_router_from_settings

pytestmark = pytest.mark.integration


def test_planner_against_live_model() -> None:
    settings = Settings()
    if not settings.api_key.get_secret_value() or not settings.base_url:
        pytest.skip("no live LLM key/base_url configured (set them in .env)")

    router = build_router_from_settings(settings)
    plan = asyncio.run(ResearchPlannerAgent(router).plan("the basics of how vaccines work"))

    assert plan.sub_questions, "live planner returned no sub-questions"
    assert all(sq.text for sq in plan.sub_questions)
