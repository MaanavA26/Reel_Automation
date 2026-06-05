"""Worked example: scoring the Research Planner task with the eval harness.

Mirrors ``docs/llm-model-selection.md`` §3 (the one fully-built LLM task, scored
across candidate models) — but hermetically: the planner's `SYSTEM_PROMPT` and
`_PlannerOutput` schema define the `EvalTask`, candidates are `FakeProvider`s
replaying scripted plans, and a `RuleBasedJudge` scores coverage by sub-question
count. This shows the harness scoring a *real engine task* without invoking the
agent (the harness core stays schema-agnostic; the agent is referenced only here,
in a test).
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.agents.research_planner import (
    SYSTEM_PROMPT,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.eval import EvalHarness, EvalTask, QualityScore, RuleBasedJudge
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice


def _planner_task(topic: str) -> EvalTask[_PlannerOutput]:
    return EvalTask(
        name=topic,
        system=SYSTEM_PROMPT,
        prompt=f"Topic to research:\n\n{topic}\n\nProduce the research plan.",
        schema=_PlannerOutput,
    )


def _coverage_score(output: BaseModel) -> QualityScore:
    """Reward broader (but capped) sub-question coverage — a §3-style rubric."""
    assert isinstance(output, _PlannerOutput)
    n = len(output.sub_questions)
    return QualityScore(score=float(min(n, 5)), rationale=f"{n} sub-questions")


def test_planner_eval_ranks_broader_plan_higher() -> None:
    topic = "the economics of desalination"
    thin = FakeProvider([_PlannerOutput(sub_questions=[_PlannerSubQuestion(text="q1")])])
    rich = FakeProvider(
        [_PlannerOutput(sub_questions=[_PlannerSubQuestion(text=f"q{i}") for i in range(5)])]
    )
    harness = EvalHarness(
        providers={"thin": thin, "rich": rich},
        judge=RuleBasedJudge(_coverage_score),
    )
    report = asyncio.run(
        harness.run(
            tasks=[_planner_task(topic)],
            candidates=[ModelChoice("thin", "m1"), ModelChoice("rich", "m2")],
        )
    )
    assert report.best().provider == "rich"
    assert report.best().mean_quality == 5.0
    # The candidate received the planner's real system prompt.
    assert rich.calls[0].system == SYSTEM_PROMPT
