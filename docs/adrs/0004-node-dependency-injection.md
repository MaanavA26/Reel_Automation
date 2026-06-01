# ADR 0004: Workflow Node Dependency Injection

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0002 fixed the node I/O contract as `async def node(state: ResearchState)
-> StateUpdate`. The M1 stub nodes were pure functions with no collaborators.
M3 introduces the first node that needs one: the `plan` node delegates to a
`ResearchPlannerAgent` (which itself holds a `ModelRouter`). Every later agent
node (Source Discovery M5, Evidence Extraction M7, Synthesis M9, Critic M10)
will likewise need its agent or tool injected.

So a pattern must be set, once: **how does a node receive its dependencies?**
The choice is hard to reverse cheaply — it shapes every node that follows and
the signature of `build_research_graph` / `run_research`.

Two mechanisms are available:

1. **LangGraph `config`** — declare `config: RunnableConfig` on the node and
   read deps from `config["configurable"]["..."]` at invocation time.
2. **Factory-closure** — a `_make_*_node(deps) -> node` factory; the node closes
   over its typed dependencies, wired at graph-build time.

## Decision

**Workflow nodes receive their collaborators by factory-closure.** A
`_make_<band>_node(dep) -> node` factory returns a node that closes over its
typed dependency; `build_research_graph(planner)` wires the bound nodes;
`run_research(state, *, planner)` injects the dependencies a run needs. The node
keeps the minimal `(state) -> StateUpdate` signature from ADR 0002.

Run-scoped data that genuinely belongs to a single invocation (e.g. a per-run
budget, or a `thread_id` once a checkpointer lands) may still be read via
`config` — added to *only the node that needs it*. This composes freely:
LangGraph passes `config` only to nodes whose signature declares it, so a node
taking `(state)` and a node taking `(state, config)` coexist in one graph
(verified against `langgraph 1.2.1`). Factory-closure is therefore not a bet
against `config` — it is "typed deps via closure, run-scoped data via config if
ever needed," with no contract churn for nodes that need neither.

## Consequences

### Positive

- Dependencies are **typed and explicit** — `_make_plan_node(planner)` is
  checked by mypy; there is no stringly-typed `config["configurable"]["planner"]`
  lookup that fails only at runtime. Matches the project's typed-contract bar.
- The agent/tool stays cleanly separated from the node: the node is orchestration
  glue in `workflows/`; the reasoning lives in `agents/research_planner.py`; the
  node merely closes over the agent and folds its result into state (CLAUDE.md §4).
- Test injection is trivial and hermetic: tests build the graph with a
  `FakeProvider`-backed planner — no monkeypatching, no global state.

### Negative

- **No global compiled-graph singleton.** The graph closes over its deps, so
  `build_research_graph` is called per dependency-set (in practice: once at app
  startup with production deps, once per test with fakes). The M1
  `get_research_graph()` `lru_cache` singleton is removed. Compilation is cheap;
  this is an acceptable trade for explicit DI.
- A typed node alias must be a `Protocol` with a *named* `state` parameter
  (`_NodeFn`), not a bare `Callable[[ResearchState], ...]`, because LangGraph's
  `_Node` protocol calls `state` by keyword and a positional-only `Callable`
  fails the structural check. Minor, documented in code.

### Neutral

- **Planner failure is unhandled in M3.** `ResearchPlannerAgent.plan` raises
  `PlannerError` on an empty plan; nothing catches it yet, so a failed plan
  aborts the run. This is consistent with the M1/M4 status seam (ADR 0002):
  M3 proves the happy path; the `FAILED` transition, retries, and error policy
  that would catch `PlannerError` are owned by the Orchestrator (M4).

## Alternatives considered

### Option A — LangGraph `config` injection

Read deps from `config["configurable"]`. **Pros:** the LangGraph-idiomatic hook;
supports different deps per invocation on one compiled graph. **Cons:**
stringly-typed and unchecked; the "adopt `config` early to avoid churn later"
argument is moot because mixed signatures coexist (a node can add `config` later
with zero impact on others); per-invocation *model* variation is already the
router policy's job, not the graph's. **Why rejected:** loses static typing for
a flexibility this design does not need.

### Option B — Module-global router/agent

Nodes import a shared router/agent at module scope. **Pros:** simplest wiring.
**Cons:** untestable without monkeypatching; hidden global state; couples node
import to provider configuration. **Why rejected:** violates testability and the
explicit-contract bar.

## References

- Related: [ADR 0002 — LangGraph Workflow Integration](0002-langgraph-workflow-integration.md)
  (node I/O contract, M1/M4 status seam) and
  [ADR 0003 — Model Router and LLM Fabric](0003-model-router-llm-fabric.md)
  (the router the planner holds).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M3 (this), M4 (Orchestrator owns error/retry).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §10/§11 (typed contracts).
