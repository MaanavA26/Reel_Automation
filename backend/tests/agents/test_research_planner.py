"""Tests for the Research Planner agent (M3).

The agent is exercised against a `FakeProvider`-backed router, so these tests
are fully hermetic (no network, no API key) and assert the agent's contract:
mapping model output to the canonical schema, priority order preservation,
schema-minted ids/timestamps, the non-empty invariant, and use of the
``PLANNING`` role.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from app.agents.research_planner import (
    SYSTEM_PROMPT,
    PlannerError,
    ResearchPlannerAgent,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _planner(output: _PlannerOutput) -> tuple[ResearchPlannerAgent, FakeProvider]:
    fake = FakeProvider([output])
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return ResearchPlannerAgent(router), fake


def test_plan_maps_output_and_preserves_order() -> None:
    output = _PlannerOutput(
        goal="Understand X",
        sub_questions=[
            _PlannerSubQuestion(text="q1", rationale="r1"),
            _PlannerSubQuestion(text="q2"),
            _PlannerSubQuestion(text="q3"),
        ],
    )
    agent, _ = _planner(output)
    plan = asyncio.run(agent.plan("topic X"))
    assert plan.goal == "Understand X"
    assert [sq.text for sq in plan.sub_questions] == ["q1", "q2", "q3"]
    assert plan.sub_questions[0].rationale == "r1"
    assert plan.sub_questions[1].rationale is None


def test_ids_and_timestamps_minted_by_schema_not_model() -> None:
    output = _PlannerOutput(sub_questions=[_PlannerSubQuestion(text="q1")])
    agent, _ = _planner(output)
    plan = asyncio.run(agent.plan("t"))
    assert re.fullmatch(r"plan_[0-9a-f]{16}", plan.id)
    assert re.fullmatch(r"sq_[0-9a-f]{16}", plan.sub_questions[0].id)
    assert plan.created_at.tzinfo is not None


def test_empty_sub_questions_raises_planner_error() -> None:
    agent, _ = _planner(_PlannerOutput(sub_questions=[]))
    with pytest.raises(PlannerError):
        asyncio.run(agent.plan("t"))


def test_uses_planning_role_and_system_prompt() -> None:
    agent, fake = _planner(_PlannerOutput(sub_questions=[_PlannerSubQuestion(text="q1")]))
    asyncio.run(agent.plan("my topic"))
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.model == "planning-model"
    assert call.system == SYSTEM_PROMPT
    assert "my topic" in call.prompt
    assert call.schema is _PlannerOutput
