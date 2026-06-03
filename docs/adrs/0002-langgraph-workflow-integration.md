# ADR 0002: LangGraph Workflow Integration and Node I/O Contract

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** Tech Lead, Council (advisor + design sub-agents: top-down / bottom-up / risk-first)
- **Supersedes:** none
- **Superseded by:** none

## Context

[ADR 0001](0001-research-state-and-provenance.md) defined the canonical
`ResearchState` Pydantic model and explicitly forward-referenced a future ADR
to "document the LangGraph integration that consumes this state shape." This is
that ADR. It lands with Milestone M1 (the workflow skeleton) per
[`docs/ROADMAP.md`](../ROADMAP.md).

Before any real agent or tool node is written (M3+), the workflow must settle
how `ResearchState` flows through a LangGraph graph, because the choice is
painful to retrofit once multiple nodes exist. Specifically:

1. **What does a node return** — a full new `ResearchState`, or a partial
   update of only the channels it changed?
2. **How do the four list channels accumulate** (`plan.sub_questions`,
   `acquisition.sources`, `acquisition.chunks`, `acquisition.evidence`) once
   fan-out appears (M5 parallel discovery, M7 parallel extraction)?
3. **Sync or async** node signatures?
4. **Persistence/checkpointer** in M1 or not?

ADR 0001 established that state is *mutable* and that nodes "return updated
state" (return-based, not in-place). It did **not** mandate returning a full
object — that concrete protocol is decided here.

### Empirical grounding

These decisions are backed by a feasibility spike run against the pinned stack
(`langgraph==1.2.1`, `pydantic==2.13.4`, CPython 3.13; CI targets 3.11). The
load-bearing observations:

- `StateGraph(ResearchState)` compiles directly — **no `TypedDict` adapter is
  required** (confirms ADR 0001 § Positive). Nodes receive a hydrated
  `ResearchState` instance.
- `CompiledStateGraph.ainvoke(...)` returns a plain **`dict`**, not a model;
  the entrypoint must re-validate it back into `ResearchState`.
- A linear graph in which a node returns a **full** state via `model_copy`
  works and preserves `id`/`created_at`.
- **Fan-out with full-state return raises** `InvalidUpdateError: At key 'id':
  Can receive only one value per step.` — i.e. returning the full state means
  every concurrent branch writes *every* channel, and LangGraph rejects
  concurrent writes to a channel that has no reducer. This is a hard crash, not
  silent corruption.
- A **partial-dict return** (only changed channels), including a partial update
  of a nested submodel (`{"acquisition": ...}`), works in linear flow and
  preserves `id`/`created_at` by construction (the node never reconstructs
  state).
- An `Annotated[list, operator.add]` reducer **does** merge concurrent appends
  on a `BaseModel` state field — but only when nodes return partial updates of
  that channel.
- A **typo'd / unknown channel key** in a partial-dict return is **silently
  dropped**, not rejected — LangGraph ignores keys that are not channels, and
  `extra='forbid'` never sees them because no `ResearchState` is constructed
  from the update.

## Decision

**We adopt LangGraph with `ResearchState` as the graph state directly, async
nodes, and a partial-state-update return protocol.** This refines (does not
supersede) ADR 0001's "return updated state."

### 1. Node I/O contract

Every node has the signature:

```python
async def <band>_node(state: ResearchState) -> StateUpdate
```

where `StateUpdate = dict[str, Any]` is a **partial dict of the changed
top-level channels**. Nodes read fully typed state but never construct a full
`ResearchState` to return. LangGraph merges the partial update into the running
state and re-validates under `extra='forbid'`.

Rationale: partial-dict is the only fan-out-compatible protocol (full-state
return crashes under concurrency, see Context), it structurally eliminates the
`id`/`created_at` regeneration trap (the node never mints those defaults), and
it is the idiomatic LangGraph pattern. The cost is the loss of a typed return
value and of typo protection on channel keys (§ Negative).

### 2. Async-first

Nodes are `async def` from M1 even though the stubs perform no I/O. Real nodes
(M3+) make concurrent LLM/network calls and will be async; shipping a sync
contract now would force a mechanical sync→async conversion across every node
later — exactly the contract churn the skeleton exists to prevent. Tests drive
the async entrypoint with `asyncio.run`, so **no `pytest-asyncio` dependency is
added**.

### 3. Graph shape and lifecycle (M1 scope)

Linear topology: `START -> plan -> acquire -> reason -> publish -> END`. The
`plan` node flips `status` `QUEUED -> RUNNING`; `publish` flips it
`-> COMPLETED`. Every node advances `updated_at`. The `acquire` stub appends one
placeholder `Source` to exercise a list-channel write end-to-end.

The graph is compiled once and cached (`get_research_graph`, mirroring
`get_settings`); a compiled graph is stateless across runs.

### 4. Checkpointer: out of M1

The graph compiles with **no checkpointer** — runs are in-memory and
non-resumable. Persistence/resumability is not a requirement yet; it is recorded
here as a deliberate "no," to be revisited when a real consumer (e.g. job
resume, long-running acquisition) needs it.

### 5. The M1/M4 status seam

M1 performs only **mechanical** `JobStatus` enum flips to prove the lifecycle
threads through state. The `FAILED`/`CANCELLED` transitions and the
*conditional/retry/budget/quality-gate logic that decides transitions* are
owned by M4 (Research Orchestrator). M1 deliberately implements neither.

### 6. Fan-out accumulation: OPEN, deferred to M5/M7

This decision is **explicitly not made here**, and partial-dict returns alone do
**not** make fan-out work. Partial-dict solves *cross-channel* collision (two
branches writing different channels). It does **not** solve *same-channel
accumulation* — M5 discovery and M7 extraction will have N branches all
appending to `acquisition.sources` / `acquisition.evidence`, which still needs
explicit handling. The resolution depends on the fan-out *topology*, chosen at
M5/M7:

- **Parallel edges + reducer.** Because the lists are nested inside `plan` /
  `acquisition` submodels, a reducer attaches to the whole `acquisition` channel
  and must merge two `KnowledgeAcquisitionState` objects (a custom reducer), or
  the lists are lifted to top-level `Annotated[list, add]` channels (which
  touches ADR 0001's state shape).
- **Send-based map-reduce.** A single aggregator node writes the channel once,
  so no reducer is needed.

Naming *why* this cannot be decided now (it is topology-contingent) is a
stronger deferral than "we will add reducers later."

## Consequences

### Positive

- Every later milestone (M3-M12) plugs into a compiled, running graph with one
  fixed node signature; the contract no longer gets re-litigated per PR.
- The expensive-to-reverse bet (state merge semantics) was validated
  empirically with throwaway stubs rather than discovered late atop real nodes.
- `id`/`created_at` regeneration is structurally impossible under the partial
  contract.
- A runnable `queued -> completed` graph (and `draw_mermaid()` diagram) is a
  showcaseable artifact (CLAUDE.md §12) and unblocks all downstream work.

### Negative

- **Typo'd / unknown channel keys are silently dropped, not rejected.** This is
  the real cost of partial-dict over full-state return. Mitigation for M1 is the
  small node count plus review; when the node count grows, a typed
  `ResearchStateUpdate` `TypedDict` (mypy-checked, listing the valid channel
  keys) can be introduced to catch typos at type-check time. Not added now (no
  consumer yet — avoids speculative abstraction per coding-standards).
- Node returns are `dict[str, Any]`, so the *return* side is not statically
  typed (reads remain fully typed; the merge re-validates under `extra='forbid'`).
- A new runtime dependency (`langgraph>=1.0,<2.0`) and its transitive tree
  (langchain-core, langgraph-checkpoint, langgraph-prebuilt, orjson, etc.).

### Neutral

- The `invoke`/`ainvoke` "returns a dict" wart is contained to a single seam in
  `run_research`, which doubles as the final strict-revalidation gate.
- `langgraph` is pinned as a range (`>=1.0,<2.0`) consistent with the existing
  dependency style; the exact-pin policy is a separate future decision.

## Alternatives considered

### Option A — Full-state return (`model_copy`-based)

Nodes return a full `ResearchState` (via `model_copy`). **Pros:** typed return
value; typo'd keys caught by `extra='forbid'`; matches a literal reading of
ADR 0001; `model_copy` itself preserves `id`/`created_at` (the spike confirmed
this). **Cons:** empirically crashes under any fan-out — returning the full
state writes *every* channel, so two concurrent branches raise
`InvalidUpdateError` on `id` — which would force a migration through every node
at M5/M7. It also keeps the door open to the `id`/`created_at` regeneration trap
that the partial contract forecloses structurally: a node that *constructs*
(`ResearchState(...)`) instead of *copies* silently mints new defaults, whereas a
partial node never builds a state object at all. **Why rejected:** the decisive
reason is the fan-out crash — it pays an expensive future migration for a
typing/typo benefit that is lost at fan-out anyway.

### Option B — Bottom-up: model router + Planner first, graph later

Build the LLM fabric (M2) and a real Research Planner agent (M3) as standalone
units, defer the graph until ≥2 real nodes exist. **Pros:** each unit
independently useful and demoable; lets the node contract be "taught" by a real
node. **Cons:** a real Planner needs the router *and* implicitly forces the node
contract anyway, producing a larger, less-reviewable change; defers the cheap
empirical validation of the state-merge bet, raising the chance it is discovered
late. **Why rejected:** the skeleton validates the expensive bet at near-zero
cost with stubs, and keeps M1 free of any provider-SDK dependency. (The
bottom-up proposal's structured-output DTO pattern and `services/llm/` placement
are adopted for M2/M3.)

### Option C — Node-contract-as-ADR-only, no graph in M1

Write the contract on paper without a running graph. **Pros:** no new
dependency yet. **Cons:** paper cannot falsify the strict-Pydantic-as-state bet;
the entire value of M1 is the empirical check (a document cannot reveal that
full-state return crashes under fan-out). **Why rejected:** the running graph is
the deliverable.

### Option D — Implement reducers / restructure schema now

Add `Annotated[list, add]` reducers (or lift the lists to top-level) in M1.
**Pros:** fan-out-ready immediately. **Cons:** speculative — M1 is linear and
has no fan-out node; the correct reducer design is topology-contingent (§
Decision 6) and would touch ADR 0001's schema shape prematurely. **Why
rejected:** building the abstraction before its consumer exists violates
CLAUDE.md §7/§13; documenting the open question is the disciplined move.

## References

- Related: [ADR 0001 — Research State and Provenance](0001-research-state-and-provenance.md)
  (state container, mutability, strict schemas; forward-references this ADR).
- [`docs/ROADMAP.md`](../ROADMAP.md) — milestone sequence (M1 skeleton, M4
  orchestrator, M5/M7 fan-out).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (model routing), §7/§13
  (no speculative overbuild).
- LangGraph concurrent-update error:
  https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE
