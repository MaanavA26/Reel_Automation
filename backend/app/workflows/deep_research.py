"""Deep Research workflow — LangGraph skeleton (Phase 0, Milestone M1).

This module wires the canonical :class:`~app.schemas.research_state.ResearchState`
through a linear sequence of band nodes (plan -> acquire -> reason -> publish)
so that every later milestone has a compiled graph and a stable node contract
to plug into.

The nodes here are **lifecycle stubs**: they advance ``status``/``updated_at``
and demonstrate the state-threading contract, but contain no real intelligence.
Real reasoning (the Research Planner agent, M3) and deterministic acquisition
(Source Discovery/Ingestion, M5-M6) replace the stub bodies in later milestones.

The node I/O contract, the partial-state-update return protocol, and the
deferred fan-out accumulation decision are documented in
``docs/adrs/0002-langgraph-workflow-integration.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

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


async def plan_node(state: ResearchState) -> StateUpdate:
    """Research Control band entrypoint. Stub: marks the job ``RUNNING``.

    The Research Planner agent (M3) replaces this body, populating
    ``state.plan`` with decomposed sub-questions.
    """
    return {"status": JobStatus.RUNNING, "updated_at": datetime.now(UTC)}


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


def build_research_graph() -> CompiledStateGraph:
    """Build and compile the linear Deep Research workflow graph.

    Topology (M1): ``START -> plan -> acquire -> reason -> publish -> END``.
    No conditional routing, retries, budgets, or checkpointer yet — the
    ``FAILED``/``CANCELLED`` lifecycle and orchestration policy land in M4
    (see ADR 0002 § the M1/M4 status seam).
    """
    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_node)
    graph.add_node("acquire", acquire_node)
    graph.add_node("reason", reason_node)
    graph.add_node("publish", publish_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "acquire")
    graph.add_edge("acquire", "reason")
    graph.add_edge("reason", "publish")
    graph.add_edge("publish", END)
    return graph.compile()


@lru_cache(maxsize=1)
def get_research_graph() -> CompiledStateGraph:
    """Return the process-wide compiled research graph (compiled once, reused).

    A compiled graph is stateless across runs — per-job state is passed into
    each invocation — so a single cached instance is safe to share. Mirrors
    the ``get_settings()`` caching pattern in ``app.core.config``.
    """
    return build_research_graph()


async def run_research(state: ResearchState) -> ResearchState:
    """Run a research job end-to-end and return the final typed state.

    ``CompiledStateGraph.ainvoke`` returns a plain ``dict``; this entrypoint
    re-validates it back into a strict ``ResearchState``, which doubles as the
    final ``extra='forbid'`` integrity gate on the merged state.
    """
    result = await get_research_graph().ainvoke(state)
    return ResearchState.model_validate(result)
