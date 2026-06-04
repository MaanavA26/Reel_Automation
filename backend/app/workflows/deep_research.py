"""Deep Research workflow — LangGraph orchestration.

This module wires the canonical :class:`~app.schemas.research_state.ResearchState`
through a sequence of band nodes (plan -> acquire -> reason -> publish) on the
happy path, with each band conditionally short-circuiting to a terminal
``failed`` sink if a node fails (M4).

The ``plan`` (M3), ``acquire`` (M5), and ``ingest`` (M6) nodes are real: bound to
a `ResearchPlannerAgent`, a `SourceDiscoveryAgent`, and an `IngestionService`
respectively via factory-closure dependency injection (ADR 0004). The
``reason``/``publish`` nodes remain **lifecycle stubs** — advancing
``status``/``updated_at`` and demonstrating the state-threading contract — until
their owning milestones replace them (M8-M12).

ADRs: node I/O contract + partial-state-update protocol + fan-out deferral in
``0002-langgraph-workflow-integration.md``; node dependency injection in
``0004-node-dependency-injection.md``; error handling + conditional routing in
``0005-workflow-error-handling.md``.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.research_planner import ResearchPlannerAgent
from app.agents.source_discovery import SourceDiscoveryAgent
from app.schemas.research_state import JobStatus, ResearchState
from app.services.ingestion.service import IngestionService

# Node I/O contract (ADR 0002): every node is
# ``async def node(state: ResearchState) -> StateUpdate`` where ``StateUpdate``
# is a partial dict of the *changed* top-level channels. Nodes read fully typed
# state but never construct a full ``ResearchState`` to return — that pattern
# regenerates ``id``/``created_at`` and collides under fan-out. LangGraph merges
# the partial update into the running state and re-validates it under
# ``extra='forbid'``. Trade-off: an unknown/typo'd channel key is silently
# dropped rather than raising (see ADR 0002 § Negative).
StateUpdate = dict[str, Any]


class _NodeFn(Protocol):
    """Type of a workflow node callable.

    The ``state`` parameter is *named* (not positional-only as a bare
    ``Callable[[ResearchState], ...]`` alias would be) so that closure-built
    nodes satisfy LangGraph's ``_Node`` protocol, whose ``__call__`` accepts
    ``state`` by keyword.
    """

    def __call__(self, state: ResearchState) -> Coroutine[Any, Any, StateUpdate]: ...


def _make_plan_node(planner: ResearchPlannerAgent) -> _NodeFn:
    """Build the Research Control band entrypoint node, bound to a planner.

    Dependency injection is by factory-closure (ADR 0004): the node closes over
    its `ResearchPlannerAgent` rather than reading it from LangGraph ``config``,
    keeping the node signature minimal and the dependency typed and explicit.
    The node populates ``state.plan`` and transitions the job to ``RUNNING``.
    """

    async def plan_node(state: ResearchState) -> StateUpdate:
        plan = await planner.plan(state.topic)
        return {
            "status": JobStatus.RUNNING,
            "plan": plan,
            "updated_at": datetime.now(UTC),
        }

    return plan_node


def _make_acquire_node(discovery: SourceDiscoveryAgent) -> _NodeFn:
    """Build the Knowledge Acquisition band node, bound to a discovery agent.

    Mirrors `_make_plan_node` (factory-closure DI, ADR 0004). The agent plans
    queries and retrieves sources via its search tool; the node writes them to
    ``acquisition.sources`` in a *single* channel write, so no fan-out reducer is
    needed yet — the reducer decision stays deferred to M7 (parallel
    extraction), per ADR 0002 §6 and ADR 0006.
    """

    async def acquire_node(state: ResearchState) -> StateUpdate:
        sources = await discovery.discover(state.plan)
        acquisition = state.acquisition.model_copy(update={"sources": sources})
        return {"acquisition": acquisition, "updated_at": datetime.now(UTC)}

    return acquire_node


def _make_ingest_node(ingestion: IngestionService) -> _NodeFn:
    """Build the Source Ingestion node (M6), bound to an ingestion service.

    Deterministic tool work (CLAUDE.md §4): fetch + parse + chunk the discovered
    sources into ``acquisition.chunks`` in a *single* channel write (fan-out
    reducer stays deferred to M7, per ADR 0002 §6 / ADR 0008).
    """

    async def ingest_node(state: ResearchState) -> StateUpdate:
        chunks = await ingestion.ingest(state.acquisition.sources)
        acquisition = state.acquisition.model_copy(update={"chunks": chunks})
        return {"acquisition": acquisition, "updated_at": datetime.now(UTC)}

    return ingest_node


async def reason_node(state: ResearchState) -> StateUpdate:
    """Knowledge Reasoning band. Lifecycle-only stub (no substate yet).

    ``KnowledgeReasoningState`` is intentionally not on the schema yet; it
    lands with its owning milestones (M8-M10).
    """
    return {"updated_at": datetime.now(UTC)}


async def publish_node(state: ResearchState) -> StateUpdate:
    """Knowledge Publishing band terminal node. Stub: marks the job ``COMPLETED``.

    ``ResearchPublishingState`` and real artifact generation land in M11-M12.
    """
    return {"status": JobStatus.COMPLETED, "updated_at": datetime.now(UTC)}


async def failed_node(state: ResearchState) -> StateUpdate:
    """Terminal sink for failed runs (ADR 0005).

    A thin sink: the failure ``status``/``error`` were already set by
    `_with_failure_handling` before routing reached here. It exists to give the
    failure path an explicit terminal node in the compiled topology (legible in
    ``draw_mermaid``); it does no recovery work — that is deferred to the
    Orchestrator's retry/quality logic (M4b+/M10).
    """
    return {"updated_at": datetime.now(UTC)}


# --- Error handling (ADR 0005) ----------------------------------------------
# Conditional edges fire only on a *successful* node return (verified against
# langgraph 1.2.1); a raised exception otherwise propagates out of the run. So
# failures must be converted to a partial-dict state update *before* routing.
# `_with_failure_handling` is that converter, applied uniformly to every band
# node to establish the error contract once (like ADR 0002 established the node
# I/O contract once) — the stub bands cannot raise today, but real bands (M5+)
# will, and they plug into the contract instead of forcing a retrofit.


def _with_failure_handling(node: _NodeFn) -> _NodeFn:
    """Wrap a node so any raised exception becomes a ``FAILED`` state update."""

    async def wrapped(state: ResearchState) -> StateUpdate:
        try:
            return await node(state)
        except Exception as exc:
            return {
                "status": JobStatus.FAILED,
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(UTC),
            }

    return wrapped


def _route_on_status(state: ResearchState) -> Literal["continue", "failed"]:
    """Deterministic router: short-circuit to the failure sink once FAILED.

    Keyed off the typed ``status`` channel (the router receives a hydrated
    ``ResearchState``, verified against langgraph 1.2.1), not off exception
    plumbing — so the transition is inspectable and testable.
    """
    return "failed" if state.status is JobStatus.FAILED else "continue"


def build_research_graph(
    planner: ResearchPlannerAgent,
    discovery: SourceDiscoveryAgent,
    ingestion: IngestionService,
) -> CompiledStateGraph:
    """Build and compile the Deep Research workflow graph.

    Topology: ``START -> plan -> acquire -> ingest -> reason -> publish -> END``
    on the happy path, with each band conditionally short-circuiting to a
    terminal ``failed`` sink if a node failed (ADR 0005). The ``plan``,
    ``acquire``, and ``ingest`` nodes are bound to their collaborators via
    factory-closure DI (ADR 0004). Retries, budgets, ``CANCELLED``, and quality
    gates are deferred (ADR 0005 § Deferred).
    """
    graph = StateGraph(ResearchState)
    graph.add_node("plan", _with_failure_handling(_make_plan_node(planner)))
    graph.add_node("acquire", _with_failure_handling(_make_acquire_node(discovery)))
    graph.add_node("ingest", _with_failure_handling(_make_ingest_node(ingestion)))
    graph.add_node("reason", _with_failure_handling(reason_node))
    graph.add_node("publish", _with_failure_handling(publish_node))
    graph.add_node("failed", failed_node)

    graph.add_edge(START, "plan")
    bands = (
        ("plan", "acquire"),
        ("acquire", "ingest"),
        ("ingest", "reason"),
        ("reason", "publish"),
    )
    for source, following in bands:
        graph.add_conditional_edges(
            source,
            _route_on_status,
            {"continue": following, "failed": "failed"},
        )
    graph.add_edge("publish", END)
    graph.add_edge("failed", END)
    return graph.compile()


async def run_research(
    state: ResearchState,
    *,
    planner: ResearchPlannerAgent,
    discovery: SourceDiscoveryAgent,
    ingestion: IngestionService,
) -> ResearchState:
    """Run a research job end-to-end and return the final typed state.

    The graph is built per dependency-set (it closes over the injected
    collaborators), so there is no global compiled singleton; callers inject the
    dependencies a run needs. ``CompiledStateGraph.ainvoke`` returns a plain
    ``dict``; this entrypoint re-validates it back into a strict ``ResearchState``,
    which doubles as the final ``extra='forbid'`` integrity gate.
    """
    graph = build_research_graph(planner, discovery, ingestion)
    result = await graph.ainvoke(state)
    return ResearchState.model_validate(result)
