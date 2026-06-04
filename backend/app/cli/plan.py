"""Run the Research Planner against a real LLM (manual live smoke test).

This is the first end-to-end exercise of a real model call: it builds the model
router from environment configuration, runs the `ResearchPlannerAgent` on a
topic, and prints the resulting `ResearchPlan` as JSON.

Usage (from the ``backend/`` directory)::

    cp .env.example .env          # then paste your free LLM API key into .env
    python -m app.cli.plan "why fusion ignition is hard"

Configuration is read from environment variables (and a local ``.env`` file)
with the ``REEL_AUTOMATION_`` prefix — see ``.env.example`` for the full set.
"""

from __future__ import annotations

import asyncio
import sys

from app.agents.research_planner import ResearchPlannerAgent
from app.core.config import Settings
from app.services.llm.factory import build_router_from_settings

_DEFAULT_TOPIC = "the James Webb Space Telescope"


async def _run(topic: str) -> None:
    # Construct Settings explicitly (not the cached module-level instance) so the
    # CLI reads the current environment / .env at invocation time.
    router = build_router_from_settings(Settings())
    plan = await ResearchPlannerAgent(router).plan(topic)
    print(plan.model_dump_json(indent=2))


def main() -> None:
    topic = " ".join(sys.argv[1:]).strip() or _DEFAULT_TOPIC
    asyncio.run(_run(topic))


if __name__ == "__main__":
    main()
