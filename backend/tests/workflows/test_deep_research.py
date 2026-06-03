"""Tests for the Deep Research LangGraph workflow.

These tests assert the *state-threading contract* every node depends on:
lifecycle transitions, job-identity stability, that a list-channel write
survives the merge, that the final state re-validates under the strict schema,
and (as of M3) that the planner-bound ``plan`` node populates ``state.plan``
end-to-end. They run the real compiled graph with a `FakeProvider`-backed
planner (hermetic — no network) and drive the async entrypoint with
``asyncio.run`` (no ``pytest-asyncio`` dependency required).
"""

from __future__ import annotations

import asyncio

from app.agents.research_planner import (
    ResearchPlannerAgent,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.schemas.research_state import JobStatus, ResearchState
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.workflows.deep_research import (
    _make_plan_node,
    build_research_graph,
    publish_node,
    run_research,
)


def _planner(sub_question_texts: tuple[str, ...] = ("q1", "q2")) -> ResearchPlannerAgent:
    """A planner backed by a fake provider returning the given sub-questions."""
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in sub_question_texts],
    )
    router = ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return ResearchPlannerAgent(router)


def _empty_planner() -> ResearchPlannerAgent:
    """A planner whose model returns no sub-questions, so plan() raises PlannerError."""
    router = ModelRouter(
        providers={"fake": FakeProvider([_PlannerOutput(sub_questions=[])])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return ResearchPlannerAgent(router)


def _run(topic: str = "t") -> ResearchState:
    return asyncio.run(run_research(ResearchState(topic=topic), planner=_planner()))


def test_graph_compiles() -> None:
    assert build_research_graph(_planner()) is not None


def test_run_research_reaches_completed() -> None:
    final = _run("quantum computing")
    assert final.status is JobStatus.COMPLETED


def test_run_research_returns_typed_state() -> None:
    # ainvoke returns a dict; the entrypoint must hand back a ResearchState.
    assert isinstance(_run(), ResearchState)


def test_job_identity_stable_across_run() -> None:
    # Partial-dict returns never reconstruct state, so id/created_at are
    # preserved by construction (ADR 0002).
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial, planner=_planner()))
    assert final.id == initial.id
    assert final.created_at == initial.created_at


def test_updated_at_advances() -> None:
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial, planner=_planner()))
    assert final.updated_at >= initial.updated_at


def test_plan_node_populates_plan() -> None:
    # M3: the plan node is bound to the planner and writes state.plan.
    final = asyncio.run(run_research(ResearchState(topic="t"), planner=_planner(("a", "b", "c"))))
    assert [sq.text for sq in final.plan.sub_questions] == ["a", "b", "c"]


def test_plan_node_transitions_to_running() -> None:
    # The QUEUED -> RUNNING transition is part of the node contract; assert it
    # at the node seam (publish later overwrites status to COMPLETED).
    node = _make_plan_node(_planner())
    update = asyncio.run(node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.RUNNING


def test_acquire_appends_source_survives_merge() -> None:
    # Regression guard for the list-channel write threading through the graph.
    assert len(_run().acquisition.sources) == 1


def test_final_state_revalidates_strict() -> None:
    final = _run()
    assert ResearchState.model_validate(final.model_dump())


def test_real_substates_present() -> None:
    # plan and acquisition exist on the schema; reason/publish are
    # lifecycle-only stubs with no substate yet (M8-M12).
    final = _run()
    assert final.plan is not None
    assert final.acquisition is not None


def test_publish_node_transitions_to_completed() -> None:
    update = asyncio.run(publish_node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.COMPLETED


# --- M4: error handling + conditional routing (ADR 0005) --------------------


def test_planner_failure_routes_to_failed() -> None:
    # A raised PlannerError is converted to a FAILED state update and
    # short-circuits the pipeline; run_research returns rather than crashing.
    final = asyncio.run(run_research(ResearchState(topic="t"), planner=_empty_planner()))
    assert final.status is JobStatus.FAILED
    assert final.error is not None
    assert "PlannerError" in final.error


def test_failed_run_short_circuits_remaining_bands() -> None:
    # Failure at plan routes to the terminal sink, so acquire never runs and
    # no placeholder source is appended.
    final = asyncio.run(run_research(ResearchState(topic="t"), planner=_empty_planner()))
    assert final.acquisition.sources == []


def test_happy_path_leaves_error_unset() -> None:
    final = _run()
    assert final.status is JobStatus.COMPLETED
    assert final.error is None
