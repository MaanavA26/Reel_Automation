"""Deep Research workflow — LangGraph orchestration.

This module wires the canonical :class:`~app.schemas.research_state.ResearchState`
through a sequence of band nodes (plan -> acquire -> ingest -> extract -> verify
-> synthesize -> critique -> report -> publish) on the happy path, with each band
conditionally short-circuiting to a terminal ``failed`` sink if a node fails (M4).

The ``plan`` (M3), ``acquire`` (M5), ``ingest`` (M6), ``extract`` (M7),
``verify`` (M8), ``synthesize`` (M9), ``critique`` (M10a), and ``report`` (M11)
nodes are real: bound to a `ResearchPlannerAgent`, a `SourceDiscoveryAgent`, an
`IngestionService`, an `EvidenceExtractionAgent`, a `CrossVerificationAgent`, a
`SynthesisAgent`, an `EditorialCriticAgent`, and a `ReportAgent` respectively via
factory-closure dependency injection (ADR 0004), bundled into a single
`ResearchDeps` container (ADR 0009). The ``critique`` node carries the graph's
first **cycle** (M10b): on ``revise`` it routes back to ``synthesize`` (feeding
the critique forward), bounded by a ``max_syntheses`` cap so the loop always
terminates (ADR 0012); on ``accept``/``exhausted`` it proceeds to ``report``. The
``publish`` node remains the **lifecycle terminal** — marking the job
``COMPLETED`` (the success mirror of the ``failed`` sink) — while the Publishing
band's artifact work lives in ``report`` (M11).

ADRs: node I/O contract + partial-state-update protocol + fan-out deferral in
``0002-langgraph-workflow-integration.md``; node dependency injection in
``0004-node-dependency-injection.md``; error handling + conditional routing in
``0005-workflow-error-handling.md``; dependency bundling in
``0009-evidence-extraction.md``; cross-verification in
``0010-cross-verification.md``; synthesis in ``0011-synthesis.md``; editorial
critic in ``0012-editorial-critic.md``; report generation in
``0017-report-generation.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.cross_verification import CrossVerificationAgent
from app.agents.editorial_critic import EditorialCriticAgent
from app.agents.evidence_extraction import EvidenceExtractionAgent
from app.agents.report import ReportAgent
from app.agents.research_planner import ResearchPlannerAgent
from app.agents.source_discovery import SourceDiscoveryAgent
from app.agents.synthesis import SynthesisAgent
from app.schemas.research_state import CritiqueDecision, JobStatus, ResearchState
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


@dataclass(frozen=True)
class ResearchDeps:
    """The agents/services the workflow nodes depend on, injected as one bundle.

    Introduced at M7 (ADR 0009) once the per-node injected-dependency count
    crossed the threshold ADR 0008 flagged — keeps ``run_research`` /
    ``build_research_graph`` to a single ``deps`` parameter instead of a growing
    list of kwargs.
    """

    planner: ResearchPlannerAgent
    discovery: SourceDiscoveryAgent
    ingestion: IngestionService
    extractor: EvidenceExtractionAgent
    verifier: CrossVerificationAgent
    synthesizer: SynthesisAgent
    critic: EditorialCriticAgent
    reporter: ReportAgent


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
    needed yet — the reducer decision stays deferred to the checkpointer
    milestone, per ADR 0002 §6 and ADR 0006.
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
    reducer stays deferred to the checkpointer milestone, per ADR 0002 §6 / ADR 0008).
    """

    async def ingest_node(state: ResearchState) -> StateUpdate:
        chunks = await ingestion.ingest(state.acquisition.sources)
        acquisition = state.acquisition.model_copy(update={"chunks": chunks})
        return {"acquisition": acquisition, "updated_at": datetime.now(UTC)}

    return ingest_node


def _make_extract_node(extractor: EvidenceExtractionAgent) -> _NodeFn:
    """Build the Evidence Extraction node (M7), bound to an extraction agent.

    Extracts grounded `Evidence` from the ingested chunks into
    ``acquisition.evidence`` in a *single* channel write. Per-chunk concurrency
    and the graph-level fan-out reducer stay deferred to the checkpointer
    milestone — the single-writer pattern needs neither (ADR 0009 / ADR 0002 §6).
    """

    async def extract_node(state: ResearchState) -> StateUpdate:
        evidence = await extractor.extract(state.acquisition.chunks, state.acquisition.sources)
        acquisition = state.acquisition.model_copy(update={"evidence": evidence})
        return {"acquisition": acquisition, "updated_at": datetime.now(UTC)}

    return extract_node


def _make_verify_node(verifier: CrossVerificationAgent) -> _NodeFn:
    """Build the Cross-Verification node (M8), bound to a verification agent.

    Opens the Knowledge Reasoning band: cross-checks the extracted `Evidence`
    into `Verdict`s (corroborated / single-source / contradicted) written to
    ``reasoning.verdicts`` in a *single* channel write. Per-cluster concurrency
    and the graph-level fan-out reducer stay deferred to the checkpointer
    milestone (ADR 0010 / ADR 0002 §6).
    """

    async def verify_node(state: ResearchState) -> StateUpdate:
        verdicts = await verifier.verify(state.acquisition.evidence)
        reasoning = state.reasoning.model_copy(update={"verdicts": verdicts})
        return {"reasoning": reasoning, "updated_at": datetime.now(UTC)}

    return verify_node


def _make_synthesize_node(synthesizer: SynthesisAgent) -> _NodeFn:
    """Build the Synthesis node (M9), bound to a synthesis agent.

    Composes the cross-checked `Verdict`s (+ the plan's sub-questions) into a
    `Synthesis` of plan-anchored `Finding`s, written to ``reasoning.synthesis``
    in a *single* channel write (a single model call — nothing to fan out; the
    reducer stays deferred to the checkpointer milestone, ADR 0011 / ADR 0002 §6).
    """

    async def synthesize_node(state: ResearchState) -> StateUpdate:
        # On a revision pass (M10b) feed the latest critique forward so
        # re-synthesis addresses it; the first pass has no critique yet.
        prior_critique = state.reasoning.critiques[-1] if state.reasoning.critiques else None
        synthesis = await synthesizer.synthesize(
            state.plan, state.reasoning.verdicts, prior_critique=prior_critique
        )
        reasoning = state.reasoning.model_copy(update={"synthesis": synthesis})
        return {"reasoning": reasoning, "updated_at": datetime.now(UTC)}

    return synthesize_node


def _make_critique_node(critic: EditorialCriticAgent) -> _NodeFn:
    """Build the Editorial Critic node (M10), bound to a critic agent.

    Closes the Knowledge Reasoning band: assesses the synthesis for coverage and
    quality and appends a `Critique` to ``reasoning.critiques`` in a *single*
    channel write. It is also the **sole writer of ``revision_iteration``**
    (M10b): each pass increments the top-level counter that bounds the
    ``critique -> synthesize`` revision cycle. The counter lives top-level (not on
    ``reasoning``) so the synthesize node's ``reasoning`` rewrite on the back-edge
    cannot re-zero it. The critic only proposes accept/revise via
    ``critique.decision``; whether a revise is *permitted* is the router's call
    (`_make_critique_router`) — model proposes, code decides (ADR 0012).
    """

    async def critique_node(state: ResearchState) -> StateUpdate:
        critique = await critic.critique(state.plan, state.reasoning.synthesis)
        reasoning = state.reasoning.model_copy(
            update={"critiques": [*state.reasoning.critiques, critique]}
        )
        return {
            "reasoning": reasoning,
            "revision_iteration": state.revision_iteration + 1,
            "updated_at": datetime.now(UTC),
        }

    return critique_node


def _make_report_node(reporter: ReportAgent) -> _NodeFn:
    """Build the Report node (M11), bound to a report agent.

    Opens the Research Publishing band: composes the reasoning output into a
    structured `Report` (model prose; code-derived citations + non-omittable
    caveats) appended to ``publishing.reports`` in a *single* channel write. It
    sits downstream of the M10b revision loop's exit (the critique router routes
    ``accept``/``exhausted`` here), so a report over an exhausted-unsatisfied
    synthesis still ships — with the unresolved-critique caveat the critic carried
    forward (ADR 0017 / ADR 0012).
    """

    async def report_node(state: ResearchState) -> StateUpdate:
        report = await reporter.generate(state.plan, state.reasoning, state.acquisition)
        publishing = state.publishing.model_copy(
            update={"reports": [*state.publishing.reports, report]}
        )
        return {"publishing": publishing, "updated_at": datetime.now(UTC)}

    return report_node


async def publish_node(state: ResearchState) -> StateUpdate:
    """Lifecycle terminal: marks a successful run ``COMPLETED``.

    The success mirror of the ``failed`` sink. The Publishing band's artifact work
    (the `Report`) is produced upstream by the ``report`` node (M11); this node
    just closes out the lifecycle. The creator packet (M12) will slot in as
    another Publishing-band node feeding this terminal.
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


# Default cap on synthesis attempts (the first synthesis + up to N-1 revisions).
# Bounds the critique -> synthesize cycle so it always terminates (ADR 0012).
DEFAULT_MAX_SYNTHESES = 2


def _make_critique_router(
    max_syntheses: int,
) -> Callable[[ResearchState], Literal["failed", "revise", "accept", "exhausted"]]:
    """Build the deterministic router for the revision loop (M10b, ADR 0012).

    The **router**, not the critic agent, owns termination: the critic only
    proposes ``ACCEPT``/``REVISE``; this router decides whether a ``REVISE`` is
    *permitted*. Once ``revision_iteration`` reaches ``max_syntheses`` it forces
    ``exhausted`` (→ publish a best-effort synthesis) regardless of the model's
    decision, so the model can never keep the loop alive — the code-incremented
    counter, not the model verdict, gates the back-edge. A revision-exhausted run
    **completes** (it is not a failure — the M8/M9 "thin result is valid"
    inversion applied to the loop); that the budget was exhausted is derivable
    from ``revision_iteration == max_syntheses`` with the last critique still
    ``REVISE``.
    """

    def route(state: ResearchState) -> Literal["failed", "revise", "accept", "exhausted"]:
        if state.status is JobStatus.FAILED:
            return "failed"
        if state.revision_iteration >= max_syntheses:
            return "exhausted"
        latest = state.reasoning.critiques[-1]
        return "revise" if latest.decision is CritiqueDecision.REVISE else "accept"

    return route


def build_research_graph(
    deps: ResearchDeps, *, max_syntheses: int = DEFAULT_MAX_SYNTHESES
) -> CompiledStateGraph:
    """Build and compile the Deep Research workflow graph.

    Topology: ``START -> plan -> acquire -> ingest -> extract -> verify ->
    synthesize -> critique -> report -> publish -> END`` on the happy path, with
    each band conditionally short-circuiting to a terminal ``failed`` sink if a
    node failed (ADR 0005). The ``plan``/``acquire``/``ingest``/``extract``/
    ``verify``/``synthesize``/``critique``/``report`` nodes are bound to their
    collaborators (from ``deps``) via factory-closure DI (ADR 0004).

    M10b adds the first **cycle**: ``critique`` routes via `_make_critique_router`
    to ``synthesize`` on ``revise`` (the back-edge), to ``report`` on
    ``accept``/``exhausted``, or ``failed`` on a critic exception. The
    ``max_syntheses`` cap bounds the cycle so it always terminates. M11's
    ``report`` node (the Publishing band) sits downstream of the loop's exit and
    feeds the ``publish`` lifecycle terminal. Retries, budgets, ``CANCELLED`` are
    deferred (ADR 0005 § Deferred).
    """
    graph = StateGraph(ResearchState)
    graph.add_node("plan", _with_failure_handling(_make_plan_node(deps.planner)))
    graph.add_node("acquire", _with_failure_handling(_make_acquire_node(deps.discovery)))
    graph.add_node("ingest", _with_failure_handling(_make_ingest_node(deps.ingestion)))
    graph.add_node("extract", _with_failure_handling(_make_extract_node(deps.extractor)))
    graph.add_node("verify", _with_failure_handling(_make_verify_node(deps.verifier)))
    graph.add_node("synthesize", _with_failure_handling(_make_synthesize_node(deps.synthesizer)))
    graph.add_node("critique", _with_failure_handling(_make_critique_node(deps.critic)))
    graph.add_node("report", _with_failure_handling(_make_report_node(deps.reporter)))
    graph.add_node("publish", _with_failure_handling(publish_node))
    graph.add_node("failed", failed_node)

    graph.add_edge(START, "plan")
    # Linear bands route on status (continue/fail). ``critique`` is excluded — it
    # routes on the editorial decision (revise/accept/exhausted) instead.
    bands = (
        ("plan", "acquire"),
        ("acquire", "ingest"),
        ("ingest", "extract"),
        ("extract", "verify"),
        ("verify", "synthesize"),
        ("synthesize", "critique"),
    )
    for source, following in bands:
        graph.add_conditional_edges(
            source,
            _route_on_status,
            {"continue": following, "failed": "failed"},
        )
    graph.add_conditional_edges(
        "critique",
        _make_critique_router(max_syntheses),
        {
            "revise": "synthesize",  # the revision back-edge (the cycle)
            "accept": "report",
            "exhausted": "report",  # best-effort report on an exhausted-unsatisfied run
            "failed": "failed",
        },
    )
    # The report node (M11) inherits the standard status routing → publish.
    graph.add_conditional_edges(
        "report",
        _route_on_status,
        {"continue": "publish", "failed": "failed"},
    )
    graph.add_edge("publish", END)
    graph.add_edge("failed", END)
    return graph.compile()


def _recursion_limit(max_syntheses: int) -> int:
    """A loose `ainvoke` backstop above the legitimate worst-case super-step count.

    The real termination guarantee is the code-incremented counter + router cap;
    this only catches a guard *bug*. It must sit comfortably above the legit max
    — the 5 pre-loop nodes + ``max_syntheses`` * (synthesize + critique) + the
    report + publish tail — so the router always fires first (a hit raises an
    uncatchable ``GraphRecursionError``, so it must never be the real terminator).
    ADR 0012 / ADR 0017.
    """
    return 5 + 2 * max_syntheses + 2 + 10  # legit worst-case + generous margin


async def run_research(
    state: ResearchState, *, deps: ResearchDeps, max_syntheses: int = DEFAULT_MAX_SYNTHESES
) -> ResearchState:
    """Run a research job end-to-end and return the final typed state.

    The graph is built per dependency-set (it closes over ``deps``), so there is
    no global compiled singleton; callers inject the dependencies a run needs.
    ``CompiledStateGraph.ainvoke`` returns a plain ``dict``; this entrypoint
    re-validates it back into a strict ``ResearchState``, which doubles as the
    final ``extra='forbid'`` integrity gate. An explicit ``recursion_limit``
    backstops the revision cycle (ADR 0012).
    """
    graph = build_research_graph(deps, max_syntheses=max_syntheses)
    result = await graph.ainvoke(state, config={"recursion_limit": _recursion_limit(max_syntheses)})
    return ResearchState.model_validate(result)
