# ADR 0012: Editorial Critic (M10a) + the deferred revision loop (M10b)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (loop-topology / critic-agent-&-schema / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M10 closes the Knowledge Reasoning band: the **Editorial Critic** —
assess the `Synthesis` (M9) for coverage and quality — and the **bounded
revision loop** ADR 0005 explicitly deferred ("quality gates/revision loops →
M10"). The loop would be the **first cycle** in an otherwise linear graph.

The council surfaced a decisive fork. The risk architect verified against the
pinned `langgraph==1.2.1` that a revision loop's terminal failure mode —
`GraphRecursionError` — is raised in the Pregel super-step loop **outside** the
node callable, so `_with_failure_handling` cannot catch it: it crashes the run
and discards all partial state. Combined with the observation that a loop
re-running the *same* verdicts through the *same* prompt is "theater"
(sophistication-without-substance, CLAUDE.md §7/§17) unless the critique is fed
forward, the loop is a materially higher risk class than any node shipped so far.

## Decision

**Carve the milestone: ship M10a (Editorial Critic as assessment, linear graph)
now; defer M10b (the bounded revision loop) to its own PR.** The carve is not a
compromise — M10a is independently valuable (a code-grounded quality assessment
M11 can surface), and isolating the first cyclic-graph + termination-bound logic
into its own reviewable PR is the right risk treatment. This mirrors ADR 0005
shipping M4's deterministic failure path and deferring the rest to consumers.

### M10a (this PR)

1. **Agent/tool split (CLAUDE.md §4).** **Coverage** — which sub-questions are
   addressed by zero findings — is a deterministic set-difference over the
   `Finding.sub_question_ids` linkage M9 records, so it is a *tool*
   (`services/reasoning/coverage.py`), never the model. The **agent**
   (`EditorialCriticAgent`) judges only what code cannot: redundancy, balance,
   clarity, and whether a finding's *prose* overstates past its code-attached
   `disputed`/`weakest_support` flags.
2. **Schema.** `CritiqueDecision` {ACCEPT, REVISE}; `QualityIssueKind`
   {REDUNDANT, IMBALANCED, OVERSTATED, UNCLEAR}; `QualityIssue` (model-authored
   `kind`+`detail`, code-attached `finding_ids`/`sub_question_ids`); `Critique`
   (`crit_`; code-derived `decision`+`uncovered_sub_question_ids`; model-authored
   `issues`+`rationale`; `critiqued_via`). `KnowledgeReasoningState.critiques` is
   a **list** — forced, not chosen: a `Critique` has required fields so it cannot
   be a `default_factory` default, and `| None` is barred by ADR 0001's
   no-None-defaults rule; the empty list is the "critic has not run" signal and
   gives M10b a per-iteration audit trail for free.
3. **§11 made structural, one layer up.** The model references findings and
   sub-questions only by *local index* into two separate numbered spaces
   (`F#`/`S#`), resolved against their own lists (out-of-range dropped; an issue
   resolving to nothing dropped) — the model cannot raise an issue about a
   finding that does not exist. The **decision is code-derived**: `REVISE` iff
   (any sub-question uncovered) OR (any quality issue raised), else `ACCEPT`. The
   model gets no field to vote, so it cannot ACCEPT past an objective coverage
   gap — the M8/M9 "model proposes, code decides the structural fact" shape.
4. **`disputed` is NOT a revise trigger.** A disputed/single-source finding is a
   *valid, already-surfaced* outcome (ADR 0010/0011's "thin support is the
   product"), and re-synthesis cannot un-dispute a contradicted topic — looping
   on it would be futile. Only coverage gaps and quality issues trigger revise; a
   *dishonestly-worded* disputed finding is a model-authored `OVERSTATED` issue,
   not a code floor.
5. **Empty/failure contract — note the inversion.** `CriticError` raises only on
   an empty synthesis (defensive — synthesize already raises on zero findings
   upstream). **"Found nothing wrong" — zero issues + full coverage — is a valid
   `ACCEPT`, not a failure** (the inverse of synthesis's empty-is-failure).
6. **Role:** reuse `ModelRole.PLANNING` (adversarial analytical evaluation, like
   cross-verification — not summarization). A critique-specific role is added
   only when policy routes it to a distinct model (ADR 0003); it does not yet.
7. **Topology stays LINEAR:** `synthesize → critique → publish` via the existing
   `_route_on_status` (no new router). `EditorialCriticAgent` is the **7th
   `ResearchDeps` field**. The critique node appends to `reasoning.critiques` in a
   single channel write and **routes forward unconditionally** — `decision` is
   *recorded, not yet routed on* (M10b consumes it). Recording a not-yet-acted-on
   field mirrors ADR 0005 shipping `error` before its consumers existed; it is a
   deliberate seam, not dead code.

### M10b (deferred — the bounded revision loop, with the gate that keeps it shut)

Gated on its own reviewable PR because it is the first cyclic-graph logic and the
first uncatchable failure mode. M10b touches graph wiring + one additive scalar —
**no change to the M10a `Critique`/reasoning schema**. It will add:

- a top-level `revision_iteration: int` on `ResearchState` (one additive
  lifecycle field, not a band-substate change; metadata
  like `error`; single-writer, incremented only by the critique node — placed
  top-level so a synthesize-node channel rewrite cannot re-zero it);
- `_route_on_critique`: `revise → synthesize` (the back-edge) only while
  `revision_iteration < cap`, else `→ publish` ("exhausted"); `accept → publish`;
  the **router**, not the agent, owns termination (model proposes revise, code
  decides whether to continue — the M8/M9 shape on the continue/stop axis);
- an explicit `recursion_limit` backstop on `ainvoke`, set loose enough that the
  code cap always fires first (the backstop crashes rather than FAILs, so it must
  never be the real terminator);
- **mandatory feed-forward:** an optional `prior_critique` param on
  `SynthesisAgent.synthesize` (additive, backward-compatible) so re-synthesis
  addresses the critique — without it the loop is theater, and the load-bearing
  test asserts the critique text appears in the re-synthesis prompt;
- **exhausted COMPLETES, not FAILs:** a best-effort synthesis ships with the
  unsatisfied critique carried forward as a non-omittable caveat (the
  "thin-is-valid" inversion applied to the loop). `FAILED` stays exception-only.

## Consequences

### Positive

- The reasoning band is complete end-to-end as an assessment pipeline
  (`… → synthesize → critique`), giving §5.6's "Orchestrator" its first
  code-grounded quality signal and M11 a quality assessment to surface.
- Coverage is structurally honest (code-derived set-difference); the decision
  can't be gamed by the model.
- The first cycle's risk (uncatchable `GraphRecursionError`, theater) is isolated
  to its own PR rather than rushed in alongside the agent + schema.
- No new router, no cycle, no termination logic in M10a → the linear graph's
  proven failure contract is untouched.

### Negative

- M10a's `decision` is computed but not yet acted on until M10b. Acknowledged as
  a deliberate seam (recorded for the report + M10b), not dead code.
- The eventual v1 loop (M10b) can only fix *synthesis-layer* defects (composition,
  overstated prose, an available-but-ignored verdict, a missing linkage); it
  **cannot** fix coverage gaps rooted in missing evidence — that needs a loop
  back to acquire/extract, which reopens the ADR 0002 §6 fan-out accumulation
  problem and is gated on the checkpointer milestone.
- One more `ResearchDeps` field (now 7) — flat frozen dataclass, low cost.

### Neutral

- Out-of-range index drops and dropped issues land in logs, not the job-level
  scalar `error`. `critiqued_via = f"critique:{model.model}"`, symmetric with the
  other bands' provenance.

## Alternatives considered

- **Full loop in one milestone (M10).** Rejected: the first cyclic graph +
  termination bound + uncatchable `GraphRecursionError` + the feed-forward
  anti-theater requirement is too much risk for one reviewable PR, and M10a
  stands alone, so the carve costs nothing.
- **Mint a `CRITIQUE`/`CRITIC` `ModelRole`.** Rejected: the ADR 0003 bar (a role
  earns its place when policy routes it to a distinct model) governs; critique
  resolves to the same model today, so reuse `PLANNING`.
- **`disputed` as a revise trigger.** Rejected: contradicts ADR 0010/0011
  (thin/disputed is a valid outcome) and is mechanically futile (re-synthesis
  can't un-dispute). It becomes a model-authored `OVERSTATED` issue only when the
  *prose* overstates.
- **Model authors the accept/revise decision.** Rejected: the structural inputs
  (coverage) are code-computable; code-deriving the decision is the §11/§4
  consistent choice and prevents ACCEPT-past-a-gap.
- **`critique: Critique | None` single field.** Rejected: barred by ADR 0001
  no-None-defaults, and a `default_factory=Critique` is impossible (required
  fields). The list is forced.

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (empty-substate
  convention; no-None-defaults), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral; the M10b acquire-loop gate), [ADR 0003](0003-model-router-llm-fabric.md)
  (role-minting bar), [ADR 0004](0004-node-dependency-injection.md) (factory-closure
  DI), [ADR 0005](0005-workflow-error-handling.md) (the deferred quality-gate this
  milestone realizes; the record-a-field-before-its-consumer precedent),
  [ADR 0010](0010-cross-verification.md) / [ADR 0011](0011-synthesis.md) (the
  local-index id guard + code-derived structural fact + thin-is-valid contract this
  agent mirrors one layer up).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent vs tool), §5.5 (Reasoning band), §5.6
  (Orchestrator), §7/§17 (no flashy complexity), §11 (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M10a (this), M10b (revision loop), M11
  (report).
