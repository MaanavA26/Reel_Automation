# ADR 0010: Cross-Verification agent + the Knowledge Reasoning band

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (schema-first / agent-boundary-&-scaling / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M8 opens the **Knowledge Reasoning band** (CLAUDE.md §5.5): take the
chunk-local `Evidence` produced by M7 extraction and cross-check it *across
sources* — corroborate claims multiple sources agree on, flag claims supported
by a single source, and detect contradictions. This is the first node that
produces **inference** (a judgment *about* evidence) rather than source-grounded
**fact**, so keeping the two structurally separate is the milestone's core
correctness concern (CLAUDE.md §11 names "no distinction between evidence and
inference" as a bad pattern).

Unlike M7 (which was schema-free), M8 introduces the first reasoning substate.
The `reason` node was a lifecycle-only stub; M8 replaces it with a real node.

## Decision

**Ship a single-node `CrossVerificationAgent` fed by a deterministic
claim-blocking tool; the agent/tool split bounds the O(N²) cross-product and the
§11 boundary is made structural twice over.**

### Schema (new `KnowledgeReasoningState`)

1. `SupportLevel` StrEnum — `CORROBORATED` / `SINGLE_SOURCE` / `CONTRADICTED`.
   Deliberately a **purely structural** axis; claim *strength* (thin vs strong)
   is carried by the separate `Verdict.confidence` float, so the two orthogonal
   dimensions never collapse into one lossy label.
2. `Verdict` — the reasoning band's unit of inference: model-authored `claim`
   (canonical/merged), `support_level`, `confidence`; code-attached `id`
   (`vd_`), `supporting_evidence_ids`, `contradicting_evidence_ids`,
   `verified_via`, `verified_at`.
3. `KnowledgeReasoningState { verdicts: list[Verdict] }`, added to
   `ResearchState.reasoning` after `acquisition` (empty-substate convention,
   ADR 0001).

**Reference evidence by id, not inline snapshot.** This is the *inverse* of
`Evidence`'s attached-snapshot pattern, and it is the correct inversion:
`Evidence`'s snapshot exists to avoid a multi-hop traversal to *raw* artifacts
(`source_url`/`chunk_text`) in a separate registry; a `Verdict`'s referent is a
*peer first-class object* in `acquisition.evidence` that is already
self-documenting, so a by-id reference costs one hop to a complete object.
Snapshotting `Evidence` into N verdicts would duplicate the very `chunk_text`
duplication ADR 0001 flagged as the storage watch-item. ADR 0001 explicitly
anticipated reasoning bands querying evidence *by id*; M8 is that consumer.

### Agent / tool split (CLAUDE.md §4)

4. **Deterministic claim-blocking is a *tool*** (`services/reasoning/claim_blocking.py`),
   not the agent and not the model. `build_claim_blocks` groups evidence whose
   claims share salient lexical tokens into candidate clusters (stdlib only:
   tokenize, stopword-filter, inverted index, union-find). It **over-groups** by
   cheap lexical overlap — the lexical *floor*; the agent does the semantic
   *ceiling*. This bounds the naive O(N²) pairwise comparison to one model call
   per bounded cluster. Embedding/similarity clustering was rejected: it needs a
   network embeddings endpoint the `ModelProvider` protocol does not model
   (ADR 0003 §Negative) and would not be offline-testable.
5. **`CrossVerificationAgent`** (`agents/cross_verification.py`) is the *agent*
   (judgment): for each cluster it asks the model which claims assert the same
   fact and whether sources corroborate/contradict. One model call per cluster.

### §11 made structural — twice

6. **Id integrity.** The model references claims only by *local index* into the
   cluster it was shown; it never emits an `ev_` id. Code resolves indices to
   real `Evidence` and **drops out-of-range indices** (the model cited evidence
   it was not shown) — the M7 unknown-source guard, generalized. A `Verdict`
   citing fabricated evidence is unrepresentable.
7. **Corroboration integrity (the council/advisor blind-spot fix).**
   `CORROBORATED` means **≥2 *distinct sources*** agree, not ≥2 evidence items —
   three evidence items from one source is one source repeating itself. The
   distinct-`source_id` count is **code-derived** from the resolved supporting
   evidence; a model that over-claims `CORROBORATED` on intra-source repetition
   is **downgraded to `SINGLE_SOURCE`** (+ log). Symmetrically, `CONTRADICTED`
   stays a model judgment but is **code-gated on at least one *resolved*
   contradicting evidence item**: a verdict labelled `CONTRADICTED` while citing
   no valid contradicting evidence (none given, or all dropped as out-of-range)
   is downgraded — "sources conflict" with nothing listed as conflicting is the
   same silent-wrong class as fabricated support. This makes the entire
   support-level axis structurally honest rather than model-trusted — the same
   "model proposes, code validates against the real set" shape as the id guard.

### Failure / empty contract (the inversion vs M7)

8. M8 does **not** treat "thin support" as failure: single-source, no-overlap,
   and sparse-evidence corpora produce **valid** `SINGLE_SOURCE` verdicts —
   flagging weak support *is* the product. The agent raises `VerificationError`
   only on **empty input** (never advance on empty; M7 already raises on zero
   extraction upstream) or **empty output** (every cluster's model call failed).
   Per-cluster model failures are tolerated (skip + log), and a verdict whose
   supporting set fully fails id-resolution is dropped.

### Wiring

9. **Role:** reuse `ModelRole.PLANNING` (analytical reasoning, like source
   discovery). A verification-specific `ModelRole` is **not** added: per
   ADR 0003 a role earns its place when the policy routes it to a *distinct
   model*, which it does not yet (it would resolve to the same configured model).
   Adding the enum value now would be a label, not differentiated routing.
10. **Node:** replace the stub `reason` node with a real `verify` node
    (`_make_verify_node`, factory-closure DI, ADR 0004), single `reasoning`
    channel write, wrapped by `_with_failure_handling` → `_route_on_status`
    (ADR 0005, inherited free). `CrossVerificationAgent` is the **5th
    `ResearchDeps` field**. New topology:
    `plan → acquire → ingest → extract → verify → publish`.

### Fan-out reducer + per-cluster concurrency: DEFERRED

Clusters are independent, so concurrent per-cluster calls are a genuine speedup.
But the single-node design writes `reasoning.verdicts` in one channel write, so
no list-channel reducer is needed — identical to M5/M6/M7. Both stay deferred to
the checkpointer milestone, where durable partial state justifies the reducer
(ADR 0002 §6 / ADR 0009).

## Consequences

### Positive

- The pipeline now turns a topic into *cross-checked claims* end-to-end
  (`plan → acquire → ingest → extract → verify`), unblocking Synthesis (M9),
  which consumes `Verdict`s.
- Inference is structurally segregated from fact (`reasoning` vs `acquisition`),
  so downstream bands cannot silently render a judgment as a primary-source fact
  — the milestone's highest-value guard (CLAUDE.md §11).
- The agent/tool split is a clean showcase of the core philosophy: a
  deterministic, unit-tested blocking *service* + a judgment *agent*.
- Corroboration is structurally honest (≥2 distinct sources, code-counted).
- The `verify` node inherits M4's failure routing with zero new error plumbing.

### Negative

- **Lexical blocking is shallow** — it over-groups on surface tokens and can
  miss paraphrases sharing no salient tokens (they become separate singletons,
  judged single-source). Accepted for v1: it verifies wiring + the agent/tool
  seam, not semantic-similarity *quality*. Embedding-based blocking is the
  deferred upgrade, gated on an embeddings provider.
- **Sequential, one call per cluster** — N singleton clusters cost N calls. The
  "all-unique → zero model calls" optimization (handle singletons in code) was
  deferred for a single uniform code path in v1; concurrency is deferred too.
- One more `ResearchDeps` field (now 5) — flat frozen dataclass, low cost.

### Neutral

- Per-cluster failures and id-drops land in logs, not the job-level scalar
  `error` (single-writer-safe). A fully empty result routes to `FAILED`.
- `verified_via = f"verification:{model.model}"`, symmetric with
  `extracted_via` / `discovered_via`.

## Deferred (with the gate that keeps each shut)

- **Per-cluster concurrency + fan-out reducer** → checkpointer milestone
  (ADR 0002 §6).
- **Embedding/semantic blocking** → an embeddings provider on the model fabric
  (not modelled yet, ADR 0003).
- **Singleton-shortcut (code-built verdict, no model call)** → a perf pass once
  call volume matters; v1 favours one uniform path.
- **Verification-specific `ModelRole`** → when the policy routes it to a distinct
  model (ADR 0003 bar).
- **Synthesis / gap analysis / revision** → M9-M10, added as fields on
  `KnowledgeReasoningState`.

## Alternatives considered

- **One agent sees ALL claims and groups + judges in one pass.** Rejected:
  unbounded context as N grows (opposite of M7's per-chunk isolation) and it
  entangles grouping with verdict unauditably.
- **Model authors the support-level / corroboration directly.** Rejected: the
  distinct-source count is a structural fact, not judgment — code-deriving it is
  the §11-consistent choice and fixes the "3 evidence, 1 source → CORROBORATED"
  silent-wrong.
- **Inline-snapshot evidence on the verdict (mirror `Evidence`).** Rejected:
  duplicates the duplicated `chunk_text`; `Evidence` is already self-documenting,
  so by-id is the cheaper, ADR-0001-anticipated reference (see Decision §3).
- **New `ModelRole.VERIFICATION`.** Rejected for v1: it would resolve to the same
  configured model — a label, not routing (ADR 0003).
- **Raise on single-source / no-contradiction.** Rejected: "thin support" is a
  legitimate verification result; only "produced nothing" raises (M5/M6/M7
  precedent).

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (provenance pattern;
  anticipated by-id reasoning references), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral), [ADR 0003](0003-model-router-llm-fabric.md) (role taxonomy
  + the bar for adding a role; embeddings not modelled),
  [ADR 0004](0004-node-dependency-injection.md) (factory-closure DI),
  [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by
  `verify`), [ADR 0009](0009-evidence-extraction.md) (the agent shape +
  code-attached provenance + `ResearchDeps` this milestone mirrors and extends).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent vs tool), §5.5 (Reasoning band), §11
  (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M8 (this), M9 (Synthesis).
