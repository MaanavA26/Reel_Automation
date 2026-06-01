"""Tests for the Deep Research LangGraph workflow skeleton (M1).

These tests assert the *state-threading contract* every later node depends on:
lifecycle transitions, job-identity stability, that a list-channel write
survives the merge, and that the final state re-validates under the strict
schema. They run the real compiled graph (there is no external service to mock
yet) and drive the async entrypoint with ``asyncio.run`` (no ``pytest-asyncio``
dependency required).
"""

from __future__ import annotations

import asyncio

from app.schemas.research_state import JobStatus, ResearchState
from app.workflows.deep_research import (
    build_research_graph,
    plan_node,
    publish_node,
    run_research,
)


def test_graph_compiles() -> None:
    assert build_research_graph() is not None


def test_run_research_reaches_completed() -> None:
    final = asyncio.run(run_research(ResearchState(topic="quantum computing")))
    assert final.status is JobStatus.COMPLETED


def test_run_research_returns_typed_state() -> None:
    # ainvoke returns a dict; the entrypoint must hand back a ResearchState.
    final = asyncio.run(run_research(ResearchState(topic="t")))
    assert isinstance(final, ResearchState)


def test_job_identity_stable_across_run() -> None:
    # Partial-dict returns never reconstruct state, so id/created_at are
    # preserved by construction (ADR 0002).
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial))
    assert final.id == initial.id
    assert final.created_at == initial.created_at


def test_updated_at_advances() -> None:
    initial = ResearchState(topic="t")
    final = asyncio.run(run_research(initial))
    assert final.updated_at >= initial.updated_at


def test_acquire_appends_source_survives_merge() -> None:
    # Regression guard for the list-channel write threading through the graph.
    final = asyncio.run(run_research(ResearchState(topic="t")))
    assert len(final.acquisition.sources) == 1


def test_final_state_revalidates_strict() -> None:
    final = asyncio.run(run_research(ResearchState(topic="t")))
    assert ResearchState.model_validate(final.model_dump())


def test_real_substates_present() -> None:
    # plan and acquisition exist on the schema; reason/publish are
    # lifecycle-only stubs with no substate yet (M8-M12).
    final = asyncio.run(run_research(ResearchState(topic="t")))
    assert final.plan is not None
    assert final.acquisition is not None


def test_plan_node_transitions_to_running() -> None:
    # Unit-level check of the QUEUED -> RUNNING transition without graph
    # instrumentation.
    update = asyncio.run(plan_node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.RUNNING


def test_publish_node_transitions_to_completed() -> None:
    update = asyncio.run(publish_node(ResearchState(topic="t")))
    assert update["status"] is JobStatus.COMPLETED
