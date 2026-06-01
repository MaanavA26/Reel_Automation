"""Deep Research workflow — LangGraph orchestration.

This module wires the canonical :class:`~app.schemas.research_state.ResearchState`
through a linear sequence of band nodes (plan -> acquire -> reason -> publish)
so that every milestone has a compiled graph and a stable node contract to plug
into.

The ``plan`` node is real as of M3: it is bound to a `ResearchPlannerAgent`
(factory-closure dependency injection, ADR 0004) and populates ``state.plan``.
The ``acquire``/``reason``/``publish`` nodes remain **lifecycle stubs** —
advancing ``status``/``updated_at`` and demonstrating the state-threading
contract — until their owning milestones replace them (M5-M12).

The node I/O contract, the partial-state-update return protocol, and the
deferred fan-out accumulation decision are documented in
``docs/adrs/0002-langgraph-workflow-integration.md``; the node dependency-
injection pattern in ``docs/adrs/0004-node-dependency-injection.md``.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.research_planner import ResearchPlannerAgent
from app.schemas.research_state import JobStatus, ResearchState, Source, SourceType

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


async def acquire_node(state: ResearchState) -> StateUpdate:
    """Knowledge Acquisition band. Stub: appends one placeholder ``Source``.

    Demonstrates that a list-channel write threads through the graph and
    survives the merge. Source Discovery/Ingestion (M5-M6) replace this body.
    """
    placeholder = Source(
        url="https://example.com",
        type=SourceType.WEB,
        title="placeholder source (M1 skeleton)",
    )
    acquisition = state.acquisition.model_copy(
        update={"sources": [*state.acquisition.sources, placeholder]},
    )
    return {"acquisition": acquisition, "updated_at": datetime.now(UTC)}


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


def build_research_graph(planner: ResearchPlannerAgent) -> CompiledStateGraph:
    """Build and compile the linear Deep Research workflow graph.

    Topology: ``START -> plan -> acquire -> reason -> publish -> END``. The
    ``plan`` node is bound to ``planner`` (factory-closure DI, ADR 0004); the
    other bands are still stubs (M5-M12). No conditional routing, retries,
    budgets, or checkpointer yet — the ``FAILED``/``CANCELLED`` lifecycle and
    orchestration policy land in M4 (see ADR 0002 § the M1/M4 status seam).
    """
    graph = StateGraph(ResearchState)
    graph.add_node("plan", _make_plan_node(planner))
    graph.add_node("acquire", acquire_node)
    graph.add_node("reason", reason_node)
    graph.add_node("publish", publish_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "acquire")
    graph.add_edge("acquire", "reason")
    graph.add_edge("reason", "publish")
    graph.add_edge("publish", END)
    return graph.compile()


async def run_research(
    state: ResearchState,
    *,
    planner: ResearchPlannerAgent,
) -> ResearchState:
    """Run a research job end-to-end and return the final typed state.

    The graph is built per dependency-set (it closes over ``planner``), so
    there is no global compiled singleton; callers inject the dependencies a
    run needs. ``CompiledStateGraph.ainvoke`` returns a plain ``dict``; this
    entrypoint re-validates it back into a strict ``ResearchState``, which
    doubles as the final ``extra='forbid'`` integrity gate on the merged state.
    """
    graph = build_research_graph(planner)
    result = await graph.ainvoke(state)
    return ResearchState.model_validate(result)
