# ADR 0011: Synthesis agent + the synthesis substate

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (schema-&-structure / agent-boundary-&-scaling / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M9 is the **Synthesis** step of the Knowledge Reasoning band
(CLAUDE.md §5.5): take the `Verdict`s produced by cross-verification (M8) and the
research plan's `SubQuestion`s, and compose them into `Finding`s — answer-units
that aggregate multiple verdicts into a statement addressed to the plan, for the
downstream report (M11) and creator packet (M12).

A `Finding` is a *second-order* inference: it sits on `Verdict`s (themselves
inference on `Evidence`), making it the most-polished, furthest-from-primary-
evidence artifact the engine produces. **Gap analysis and quality judgment are
M10 (Editorial Critic), not M9** — M9 synthesizes; it does not critique its own
coverage.

## Decision

**Ship a single-node `SynthesisAgent`: one model call over the reduced verdict
set, pure agent (no new tool), with the §11 boundary made structural one layer
up and the grounding summary code-derived.**

### Schema (additive)

1. `Finding` (`fnd_`): model-authored `statement` + optional `detail`;
   code-attached `sub_question_ids` + `supporting_verdict_ids` (resolved from
   local indices, validated); **code-derived** `disputed` (any cited verdict is
   `CONTRADICTED`) + `weakest_support` (floor over the cited verdicts'
   `support_level`). `synthesized_at` / `synthesized_via` code/schema-minted.
2. `Synthesis` container `{ findings: list[Finding] }` on
   `KnowledgeReasoningState.synthesis` (default_factory). No id (band substate).

### Single model call, not map-reduce

Synthesis is **one model call over all verdicts**. This is *not* the
"one agent sees ALL claims and groups + judges in one pass" that ADR 0010
rejected for verification — that rejection was about unbounded grouping of *raw
evidence* (context grows with chunk-N; grouping entangled with judgment). Here
the input is the **already-reduced** verdict set (≈one per claim cluster, short
canonical claims, no `chunk_text` inlined), and synthesis is inherently
*holistic* — a coherent answer must span verdicts. Map-reduce (per-sub-question
then combine) buys nothing at v1 volumes and adds a reducer + variable call
count; it is deferred, gated on *verdict volume exceeding the synthesis model's
context budget*.

### Pure agent — no deterministic tool

Unlike M8 (claim-blocking earned its place by bounding an O(N²) explosion), M9
has no combinatorial explosion to bound. The tempting "group verdicts by
sub-question" step is *not* deterministic — associating a verdict to a
sub-question is the semantic judgment that *is* the agent's work. Only an inline
stable sort would be deterministic, and it is not even needed (the single call
sees the verdicts as given). So: no `services/reasoning/` module for M9.

### §11 made structural — one layer up (id integrity + grounding integrity)

The model authors prose + *local indices* only. Two **separate** index spaces —
sub-questions (`S#`) and verdicts (`V#`) — are carried as separate DTO fields and
resolved against their own lists, so a verdict index can never be misread as a
sub-question (the two-index-space hazard). Out-of-range indices are dropped +
logged; a finding with no resolvable supporting verdict is dropped (`None`) — a
finding must rest on ≥1 real verdict (the M8 drop-empty guard, generalized).

The **grounding summary is code-derived, the model gets no field to self-report
it**: `disputed` and `weakest_support` are computed from the cited verdicts'
`support_level`. This is the milestone's keystone — it makes "a finding overstates
its grounding (presents a contradicted verdict as settled)" *structurally
unrepresentable* for the grounding flag, so the caveat travels to the report
**non-omittably**. The exact analog of M8 code-deriving the distinct-source count
instead of trusting the model. (Prose fidelity — whether the *sentence* overstates
— is a prompt concern, best-effort; code guarantees the flag, not the wording.)

### Wiring

3. **Role:** `ModelRole.LONG_CONTEXT` (CLAUDE.md §6 "long-context summarization"
   — synthesis reads the whole verdict set). It resolves to the same configured
   model as `PLANNING` today, so the choice costs nothing at runtime and is a
   forward-flexible label (ops can reroute synthesis to a larger-context model
   via config, no code change). NB: the ADR 0003 bar ("a role earns its place
   when policy routes it to a distinct model") governs *minting a new role*;
   `LONG_CONTEXT` already exists, so this is *reuse* — the bar does not apply, and
   the question is only which existing role labels the work honestly. M8 reused
   `PLANNING` because verification is analytical reasoning; M9 picks
   `LONG_CONTEXT` because synthesis is summarization. Different work, different
   honest label.
4. **Node:** `synthesize` between `verify` and `publish` (`_make_synthesize_node`,
   factory-closure DI, ADR 0004), single `reasoning` channel write, wrapped by
   `_with_failure_handling` → `_route_on_status` (ADR 0005). `SynthesisAgent` is
   the **6th `ResearchDeps` field**. New topology:
   `plan → acquire → ingest → extract → verify → synthesize → publish`.

### Failure / empty contract

Raise `SynthesisError` only on **empty input** (defensive — verify already raises
on zero verdicts upstream) or **empty output** (the call failed, the model
returned none, or every finding was dropped). A synthesis over only
`SINGLE_SOURCE` or `CONTRADICTED` verdicts is **thin-but-valid** — a weak or
disputed topic is a real research outcome, surfaced via the grounding flags, not
a failure (the M8 "thin support is the product" inversion, carried forward).
Because it is a single call over a reduced input, there is no per-item
skip-and-continue (unlike M8's per-cluster tolerance): a whole-call failure →
empty output → raise.

### Narrative layer: deliberately DEFERRED

An emergent narrative layer (`narrative_summary`, `key_takeaways`) was proposed
for downstream M11/M12 convenience but **deferred**. It would be *ungrounded
model prose* — exactly the self-report this milestone is built to deny, at the
most-downstream surface where the worst silent-wrong lives. Following the repo's
"defer a future-consumer field until the consumer exists, with a named gate"
discipline (cf. `Chunk.parsed_via`, ADR 0008; `ModelRole.VERIFICATION`,
ADR 0010), it lands with M11/M12, owned by the publishing band, not shipped
speculatively now.

### Fan-out reducer / concurrency: DEFERRED

A single model call has nothing to fan out, and the single `reasoning` channel
write needs no list-channel reducer — consistent with M5-M8. Deferred to the
checkpointer milestone (ADR 0002 §6).

## Consequences

### Positive

- The pipeline now turns a topic into *synthesized, plan-anchored findings*
  end-to-end (`plan → … → verify → synthesize`), unblocking the publishing band
  (M11 report consumes `Finding`s).
- The §11 boundary holds one layer up: a finding cannot cite a verdict the model
  invented, nor overstate its grounding past what its verdicts support — the
  contradiction/weakness flag is carried forward non-omittably.
- The `synthesize` node inherits M4's failure routing with zero new error
  plumbing.
- No arbitrary aggregation: `weakest_support` is a clean floor (vs a
  hand-weighted confidence formula, which was rejected).

### Negative

- **Lexical/semantic shallowness moves up a layer**: synthesis quality depends on
  the model's composition; code guarantees grounding *integrity*, not synthesis
  *quality*. Quality judgment is M10's job by design.
- **Prose can still overstate** an individual finding's certainty — code carries
  the flag but cannot rewrite the sentence. Acknowledged, not hidden.
- One more `ResearchDeps` field (now 6) — flat frozen dataclass, low cost.
- Single-call synthesis will not scale to very large verdict sets; map-reduce is
  the deferred upgrade with a named gate.

### Neutral

- Out-of-range index drops and dropped findings land in logs, not the job-level
  scalar `error` (single-writer-safe). A fully empty result routes to `FAILED`.
- `synthesized_via = f"synthesis:{model.model}"`, symmetric with
  `verified_via` / `extracted_via` / `discovered_via`.

## Deferred (with the gate that keeps each shut)

- **Narrative layer** (`narrative_summary` / `key_takeaways`) → M11/M12 (a real
  publishing consumer).
- **Map-reduce synthesis** → verdict volume > synthesis model context budget.
- **Fan-out reducer / concurrency** → checkpointer milestone (ADR 0002 §6).
- **Verification/synthesis-specific `ModelRole`s** → when the policy routes them
  to distinct models (ADR 0003 bar).
- **Coverage / gap analysis over `sub_question_ids`** → M10 (Editorial Critic)
  *reads* the linkage; M9 only *records* it.

## Alternatives considered

- **Map-reduce (per-sub-question synthesis + combine).** Rejected for v1: no
  benefit at current volumes, adds a reducer + variable call count; deferred with
  a volume gate.
- **Model authors the grounding/confidence directly.** Rejected: a self-reported
  grounding untethered from the cited verdicts is the exact silent-wrong M8
  closed; code-deriving `disputed`/`weakest_support` is the §11-consistent choice.
- **Aggregated-confidence float on `Finding`.** Rejected: any weighting formula is
  arbitrary; `weakest_support` (a clean floor) plus the cited verdicts'
  by-id-reachable confidences carry the same information honestly.
- **Ship the narrative layer now.** Rejected: ungrounded prose at the most-
  downstream surface; deferred to its consumer (see Decision).
- **Reuse `PLANNING` (M8 consistency).** Rejected: synthesis is summarization, not
  planning; `LONG_CONTEXT` is the honest existing label and is reroutable.
- **Flat `findings` on `KnowledgeReasoningState` (no container).** Rejected: the
  `Synthesis` container gives the deferred narrative layer a home and reads as a
  cohesive band artifact distinct from the raw `verdicts`.

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (provenance pattern;
  by-id reasoning references), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral), [ADR 0003](0003-model-router-llm-fabric.md) (role taxonomy +
  the minting bar), [ADR 0004](0004-node-dependency-injection.md) (factory-closure
  DI), [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by
  `synthesize`), [ADR 0008](0008-source-ingestion-and-fetch-fabric.md) (the
  defer-a-future-consumer-field discipline), [ADR 0010](0010-cross-verification.md)
  (the agent shape + local-index id guard + code-derived structural fact + thin-
  is-valid contract this milestone mirrors and extends one layer up).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent vs tool), §5.4 (output expectations),
  §5.5 (Reasoning band), §6 (model roles), §11 (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M9 (this), M10 (Editorial Critic).
