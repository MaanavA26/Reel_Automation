# ADR 0005: Workflow Error Handling and Conditional Routing

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Tech Lead, Council (deterministic / agent / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0002 §5 reserved an "M1/M4 status seam": M1 did mechanical status flips on
the happy path; M4 (Research Orchestrator) owns the logic that *decides*
transitions, including failure. ADR 0004 §Neutral flagged the concrete gap:
`ResearchPlannerAgent.plan` raises `PlannerError`, and **nothing catches it** —
an empty-plan failure currently propagates out of `run_research` and crashes the
run. M4 closes that gap. It is also the first milestone that needs **conditional
edges** — the first departure from the linear graph M1–M3 assumed.

The ROADMAP scopes M4 broadly ("job lifecycle, status transitions, budgets,
retries, progress, quality gates"). A design council (deterministic-state-machine
vs orchestrator-as-agent vs risk-first referee) converged on a sharp narrowing,
summarized in the Decision.

### The agent-vs-deterministic question (resolved)

CLAUDE.md §3.1 places orchestrators in the "Agentic Intelligence Layer" and §5.6
names a "Research Orchestrator Agent." Does M4 introduce an agent? **No — and
not because a state machine "wins," but because the agent's raw material does
not exist yet.** Quality gates, gap analysis, and revision-loop control are
genuine judgment, but they judge Reasoning/Publishing output (`Evidence`,
contradictions, synthesis) that lands in M8–M12. Building a gate now would judge
empty evidence — textbook speculative overbuild (CLAUDE.md §7/§13). The §5.6
"Orchestrator Agent" is aspirational; the judgment node (Editorial Critic, with
a new critique `ModelRole` and a typed decision written to state that a *dumb*
deterministic switch reads) lands with M10, its first real consumer. **M4 ships
only the deterministic failure path.**

### Empirical grounding

Verified against the pinned stack (`langgraph==1.2.1`, CPython 3.13):

- **Conditional edges fire only on a node's *successful* return.** A node that
  raises propagates the exception out of `ainvoke`/`run_research` and crashes the
  run — `add_conditional_edges` does **not** catch it. Therefore a failure must
  be converted to a state update *before* routing can act on it.
- **The routing function receives a hydrated `ResearchState`** (not a plain
  dict), so routing keys off `state.status` (attribute access). This mirrors
  ADR 0002's "ainvoke returns a dict" finding — the analogous load-bearing fact
  here is the opposite for the *router* input.
- A `FAILED` status set via a partial-dict return survives the merge, the router
  routes to the terminal sink, and the final state re-validates under
  `extra='forbid'`.

## Decision

**M4 adds a deterministic failure path, entirely in `workflows/deep_research.py`
plus one schema field.** Four parts:

1. **`error: str | None` on `ResearchState`.** Set when `status` becomes
   `FAILED`. Top-level lifecycle metadata, not a band substate.

2. **`_with_failure_handling(node)` wrapper.** Wraps every band node; converts
   any raised exception into the partial-dict update
   `{"status": FAILED, "error": "<Type>: <msg>", "updated_at": ...}`. Applied
   *uniformly* to all bands — establishing the error contract once (as ADR 0002
   established the node I/O contract once), so real bands (M5+) plug into it
   rather than triggering a retrofit. The stub bands cannot raise today; the
   wrapper is a no-op for them.

3. **`_route_on_status` shared conditional router.** Keyed off the typed
   `status` channel: `FAILED` → `"failed"`, else `"continue"`. Registered via
   `add_conditional_edges` on each band transition (plan→acquire, acquire→reason,
   reason→publish).

4. **`failed` terminal sink** → `END`. A thin node (bumps `updated_at`); the
   failure `status`/`error` were already set before routing. It gives the
   failure path an explicit, diagram-legible terminal (CLAUDE.md §12); it does
   no recovery.

### Two phantom ADR-0001 conflicts, pre-empted

- **"No `None` defaults" does not apply to `error`.** That ADR 0001 rule governs
  *band substates* (don't default `acquisition` to `None` — verifiable in the
  `ResearchState` docstring: "no `None` defaults for band fields"). `error` is
  top-level lifecycle metadata, not a band substate. ADR 0001 §Consequences
  states the governing principle directly: when a lifecycle distinction "becomes
  operationally meaningful," the response is "an explicit [field] ... not
  retroactively introducing `None` defaults." ADR 0001 framed that for a
  `band_status` enum; the same reasoning licenses a top-level nullable `error`
  for a `FAILED` job.
- **`error` is a human-readable `str`, not a typed error taxonomy.** With one
  failure source (`PlannerError`) today, a taxonomy is premature.

## Consequences

### Positive

- `PlannerError` (and any future node failure) now yields a terminal `FAILED`
  state with a diagnosable `error`, instead of crashing the run — closing the
  gap ADR 0004 flagged.
- The error contract is established once and applies uniformly; M5+ real bands
  inherit failure handling for free.
- Routing is off the inspectable, typed `status` channel — testable without
  reaching into exception plumbing. The compiled graph's `draw_mermaid` now
  shows the failure path explicitly.

### Negative

- **`_with_failure_handling` catches broad `Exception`**, so a programming bug in
  a node is recorded as `FAILED` rather than surfacing as a crash. Mitigated by
  capturing `type(exc).__name__` in `error` and a test asserting the exact
  failure mode; a finer transient-vs-permanent classification arrives with
  retries + real providers (M-LP).
- **The scalar `error` is single-writer-safe only under the linear graph.** Once
  fan-out lands (M5/M7), two concurrently-failing branches both writing `error`
  hit the same `InvalidUpdateError` ADR 0002 found for `id`. This is **not fixed
  now** (that is the rejected "build the abstraction before its consumer"); it is
  named as the same topology-contingent class as ADR 0002 §6 and resolves with
  the fan-out reducer/aggregator decision.

### Neutral

- `publish` routes unconditionally to `END` (it is the last band); a publish-time
  failure still ends the run with `status=FAILED` + `error` set, just without
  passing through the `failed` sink. Acceptable for a terminal band.

## Deferred (with reasons)

Each item fails the carve test "does it have a consumer or judge-able input
today?":

- **Retries** — no flaky/transient failure source under the offline
  `FakeProvider`; `PlannerError` on an empty plan is deterministic, so a retry is
  dead code. Hook: `add_node(..., retry_policy=RetryPolicy(...))`
  (`from langgraph.types import RetryPolicy`, import path verified) when M-LP
  brings real network faults — it retries *inside* the node and composes with
  the wrapper (which still converts terminal failures).
- **Budgets** — no token/cost metering and no live provider (M-LP). Run-scoped
  budget *limits* flow via `config` per ADR 0004; the consumption tally is
  persisted state added with its consumer.
- **Progress tracking** — no consumer until the streaming API (M13).
- **`CANCELLED`** — needs an external cancel signal + a checkpointer (ADR 0002 §4
  deferred the checkpointer). The router extends to a `"cancelled"` branch
  trivially when that lands.
- **Quality gates / revision loops** — judgment over Reasoning/Publishing output
  that does not exist until M8–M12; the Editorial Critic agent owns it at M10.
- **Error fan-out aggregation** — topology-contingent, same class as ADR 0002 §6.

## Alternatives considered

### Option A — In-node guards instead of a conditional edge

Catch in `plan_node`, then have every downstream node early-return
`if status == FAILED`. **Pros:** no topology change. **Cons:** smears lifecycle
control across every node (CLAUDE.md §11 anti-pattern: mixing orchestration into
node bodies); the skip logic is invisible in the graph. **Why rejected:** one
conditional edge centralizes the failure transition and keeps it in the topology.

### Option B — Lean on LangGraph `RetryPolicy` for the FAILED transition

**Pros:** built-in. **Cons:** `RetryPolicy` re-invokes a node and then
*re-raises* — it does the retry, not the terminal-state conversion; it is
orthogonal to FAILED-routing. **Why rejected:** it cannot, by itself, turn an
exception into `FAILED`; the wrapper is still required. Retries are deferred
regardless (see above).

### Option C — Build the Orchestrator as an agent now

Model the §5.6 "Research Orchestrator Agent" with quality gates immediately.
**Pros:** matches the aspirational name. **Cons:** there is nothing to judge
until M8–M12; it would gate empty evidence. **Why rejected:** premature
(§7/§13). The judgment node lands at M10 with its raw material; M4 installs the
conditional-edge *seam* it will plug into.

## References

- Related: [ADR 0002](0002-langgraph-workflow-integration.md) (M1/M4 status seam,
  partial-dict contract, fan-out deferral pattern), [ADR 0004](0004-node-dependency-injection.md)
  (deferred `PlannerError` catch; agents/ vs workflows/ split), [ADR 0001](0001-research-state-and-provenance.md)
  (§Neutral on adding lifecycle fields).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M4 (this), M5/M7 (fan-out), M10 (Editorial
  Critic agent), M13 (API/progress), M-LP (real providers/retries).
- [CLAUDE.md](../../CLAUDE.md) §3.1, §4, §5.5, §5.6, §7/§13, §11, §12.
