"""Tests for the Deep Research LangGraph workflow.

These tests assert the *state-threading contract* every node depends on:
lifecycle transitions, job-identity stability, that list-channel writes survive
the merge, that the final state re-validates under the strict schema, and that
the real ``plan`` (M3) and ``acquire`` (M5) nodes populate state end-to-end.
They run the real compiled graph with `FakeProvider`-backed agents and a
`FakeSearchProvider` (hermetic — no network) and drive the async entrypoint with
``asyncio.run`` (no ``pytest-asyncio`` dependency required).
"""

from __future__ import annotations

import asyncio

from app.agents.research_planner import (
    ResearchPlannerAgent,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.agents.source_discovery import (
    SourceDiscoveryAgent,
    _DiscoveryOutput,
    _DiscoveryQuery,
)
from app.schemas.research_state import JobStatus, ResearchState, SourceType
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider
from app.workflows.deep_research import (
    _make_plan_node,
    build_research_graph,
    publish_node,
    run_research,
)


def _router(output: _PlannerOutput | _DiscoveryOutput) -> ModelRouter:
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )


def _planner(sub_question_texts: tuple[str, ...] = ("q1", "q2")) -> ResearchPlannerAgent:
    """A planner backed by a fake provider returning the given sub-questions."""
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in sub_question_texts],
    )
    return ResearchPlannerAgent(_router(output))


def _empty_planner() -> ResearchPlannerAgent:
    """A planner whose model returns no sub-questions, so plan() raises PlannerError."""
    return ResearchPlannerAgent(_router(_PlannerOutput(sub_questions=[])))


def _discovery(n_sources: int = 2) -> SourceDiscoveryAgent:
    """A discovery agent: model emits one query, fake search returns n sources."""
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
            for i in range(n_sources)
        ]
    )
    return SourceDiscoveryAgent(_router(output), search)


def _run(topic: str = "t") -> ResearchState:
    return asyncio.run(
        run_research(ResearchState(topic=topic), planner=_planner(), discovery=_discovery())
    )


def test_graph_compiles() -> None:
    assert build_research_graph(_planner(), _discovery()) is not None


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
    final = asyncio.run(run_research(initial, planner=_planner(), discovery=_discovery()))
    assert final.id == initial.id
    assert final.created_at == initial.created_at


def test_updated_at_advances() -> None:
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial, planner=_planner(), discovery=_discovery()))
    assert final.updated_at >= initial.updated_at


def test_plan_node_populates_plan() -> None:
    # M3: the plan node is bound to the planner and writes state.plan.
    final = asyncio.run(
        run_research(
            ResearchState(topic="t"), planner=_planner(("a", "b", "c")), discovery=_discovery()
        )
    )
    assert [sq.text for sq in final.plan.sub_questions] == ["a", "b", "c"]


def test_acquire_node_populates_sources() -> None:
    # M5: the acquire node is bound to the discovery agent; the sources it
    # produces survive the single channel write (reducer-deferral regression
    # guard — ADR 0006).
    final = asyncio.run(
        run_research(
            ResearchState(topic="t"), planner=_planner(), discovery=_discovery(n_sources=3)
        )
    )
    assert len(final.acquisition.sources) == 3
    assert all(s.discovered_via == "search:fake" for s in final.acquisition.sources)


def test_plan_node_transitions_to_running() -> None:
    # The QUEUED -> RUNNING transition is part of the node contract; assert it
    # at the node seam (publish later overwrites status to COMPLETED).
    node = _make_plan_node(_planner())
    update = asyncio.run(node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.RUNNING


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
    final = asyncio.run(
        run_research(ResearchState(topic="t"), planner=_empty_planner(), discovery=_discovery())
    )
    assert final.status is JobStatus.FAILED
    assert final.error is not None
    assert "PlannerError" in final.error


def test_failed_run_short_circuits_remaining_bands() -> None:
    # Failure at plan routes to the terminal sink, so acquire never runs and
    # no source is discovered.
    final = asyncio.run(
        run_research(ResearchState(topic="t"), planner=_empty_planner(), discovery=_discovery())
    )
    assert final.acquisition.sources == []


def test_happy_path_leaves_error_unset() -> None:
    final = _run()
    assert final.status is JobStatus.COMPLETED
    assert final.error is None


def test_discovery_failure_routes_to_failed() -> None:
    # M5 is the first *real* band that can raise. This demonstrates (not just
    # asserts) M4's bet: the uniform failure wrapper + conditional routing
    # convert a real acquire-band exception (DiscoveryError, from empty search)
    # into FAILED and short-circuit — the contract ADR 0005 established uniformly.
    final = asyncio.run(
        run_research(
            ResearchState(topic="t"), planner=_planner(), discovery=_discovery(n_sources=0)
        )
    )
    assert final.status is JobStatus.FAILED
    assert final.error is not None
    assert "DiscoveryError" in final.error
