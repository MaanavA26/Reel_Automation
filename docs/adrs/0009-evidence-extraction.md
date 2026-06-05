# ADR 0009: Evidence Extraction agent + the `ResearchDeps` container

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (agent-boundary / fan-out-topology / dependency-shape architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M7 produces the `Evidence` the Knowledge Reasoning band (M8+)
consumes: it turns the `Chunk`s produced by M6 ingestion into `Evidence` —
factual claims, each carrying a confidence score and an inline provenance
snapshot back to the source it came from. The `Evidence` schema (id, claim,
source_id, source_url, chunk_id, chunk_text, confidence, extracted_at,
extracted_via) and the `KnowledgeAcquisitionState.evidence` channel already
exist (ADR 0001), so M7 is **schema-change-free**. (Extraction writes into the
acquisition substate where `Evidence` is schema-grouped; cross-source
*reasoning* over that evidence begins at M8.)

Two questions had to be settled:

1. **Where does the agent/tool boundary fall, and how is the §11
   evidence-vs-inference rule made structural for extraction?**
2. **Topology:** M6 (ADR 0008 §Negative) flagged two follow-ups *triggered at
   M7* — the fan-out reducer for parallel per-chunk work, and a `ResearchDeps`
   container once the injected-kwarg count crossed a threshold. Are they due now?

## Decision

**Ship a single-node, sequential `EvidenceExtractionAgent`; the model authors
only `claim` + `confidence`; introduce `ResearchDeps`; keep the fan-out reducer
deferred.**

1. **`EvidenceExtractionAgent(router)`** (`agents/evidence_extraction.py`) — an
   *agent* (judgment, CLAUDE.md §4): reading a chunk and deciding which factual
   claims it supports, and how strongly, is reasoning, not a deterministic
   transform. It resolves the model via the `EXTRACTION` role of the fabric
   (ADR 0003).
2. **The §11 boundary is made structural.** The model emits only a transient DTO
   (`_ExtractedClaim {claim, confidence}`); **all provenance is code-attached**
   from the real `Chunk`/`Source` (`source_id`, `source_url`, `chunk_id`,
   `chunk_text`) — never the model. A claim therefore *cannot* be misattributed
   to a source it did not come from: misattribution is unrepresentable, not
   merely discouraged. This mirrors the M3 planner (ids/timestamps schema-minted)
   and the M5 discovery agent (LLM never mints a `Source.url`).
3. **Per-chunk isolation.** Each chunk is extracted in its own model call seeing
   only that chunk's text — bounding hallucination to chunk-supported claims, and
   keeping each call's context small. Whether a claim is *corroborated or
   contradicted across sources* is explicitly **not** this agent's job; that is
   Cross-Verification (M8). M7 produces chunk-grounded evidence, not verified
   truth.
4. **Failure contract, inherited and extended.** Per-chunk model failures are
   tolerated (skip + log) so one bad chunk cannot fail the band; a chunk
   referencing an unknown source raises `ExtractionError` (a programming/wiring
   bug, not bad input); and **zero total evidence raises** — mirroring the M5/M6
   "never advance on empty" contract. The `extract` node is wrapped by
   `_with_failure_handling` and plugs into the `_route_on_status` chain (ADR 0005)
   with zero new error plumbing.
5. **`extract` node** between `ingest` and `reason` (`_make_extract_node`,
   factory-closure DI, ADR 0004), single `acquisition.evidence` channel write.
   New topology: `plan → acquire → ingest → extract → reason → publish`.

### `ResearchDeps` container: introduced now (the M6-flagged trigger fired)

ADR 0008 §Negative pre-committed to introducing a dependency container "at M7
when the [injected-kwarg] count crosses the threshold." M7 would be the **fourth**
collaborator (`planner`, `discovery`, `ingestion`, `extractor`) threaded through
`run_research`/`build_research_graph` as positional/keyword args — so the trigger
has fired. We add a frozen `@dataclass ResearchDeps` bundling the four, and both
entrypoints take a single `deps: ResearchDeps`. This is a deliberate,
pre-registered abstraction (not speculative): it was gated on a real count, and
that gate is now met. Cost: every call site updates to `deps=...` (mechanical;
fully test-covered).

### Fan-out reducer + per-chunk concurrency: still DEFERRED

The single-node sequential design writes `evidence` in one channel write, so —
exactly as for M5 sources and M6 chunks — **no list-channel reducer is needed**.
Concurrent per-chunk extraction (a genuine speedup, since calls are independent)
and the graph-level fan-out reducer it would require remain deferred to the
**checkpointer milestone** (ADR 0002 §6), where durable partial state makes
fan-out worth its complexity. The single-writer pattern needs neither today, and
introducing a reducer now would be the speculative abstraction ADR 0008
explicitly avoided. The repeated "deferred to M7" notes in the M5/M6 code that
referred to *the reducer* are retargeted to the checkpointer milestone; the
*`ResearchDeps`* follow-up is the one that genuinely landed at M7.

## Consequences

### Positive

- The pipeline now turns a topic into *grounded `Evidence`* end-to-end
  (`plan → acquire → ingest → extract`), unblocking the reasoning band (M8
  Cross-Verification consumes `Evidence`).
- The §11 evidence-vs-inference boundary is structural, not advisory — the third
  agent (after M3, M5) to enforce it by construction.
- `ResearchDeps` collapses a growing kwarg list to one parameter, and gives every
  later node a single, typed place to receive collaborators.
- The `extract` node inherits M4's failure routing with zero new error plumbing —
  again validating ADR 0005's "real bands plug into the contract" promise.
- No schema change → no construction-site churn (dodges the "mypy doesn't
  validate Pydantic required fields" trap).

### Negative

- **Sequential extraction is slower** than concurrent per-chunk calls on
  many-chunk jobs. Accepted for v1 (correct + simple); the concurrency win is
  banked for the checkpointer milestone, where the reducer it needs is justified.
- **Per-chunk isolation can miss cross-chunk claims** (a claim split across two
  chunks). Accepted: cross-chunk/cross-source reasoning is M8/M9's job by design;
  M7 deliberately stays chunk-local to bound hallucination.
- `ResearchDeps` adds one indirection; mitigated by it being a flat frozen
  dataclass with four typed fields.

### Neutral

- Per-chunk model failures land in logs, not the job-level scalar `error` (which
  stays single-writer-safe). A fully empty extraction routes to `FAILED`.
- `extracted_via` carries the resolved model id (`f"extraction:{model.model}"`),
  symmetric with `Source.discovered_via` — provenance of *which model* extracted,
  for later audit.

## Deferred (with the gate that keeps each shut)

- **Per-chunk concurrency + fan-out reducer** → checkpointer milestone, where
  durable partial state justifies the reducer complexity (ADR 0002 §6).
- **Cross-chunk / cross-source claim reasoning** → M8 (Cross-Verification),
  M9 (Synthesis) — corroboration, contradiction, weak-support detection.
- **Claim deduplication / normalization across chunks** → M8/M9, once there is a
  verification step that benefits from canonical claims.

## Alternatives considered

- **Model returns provenance fields itself.** Rejected: violates §11 — an LLM
  could mint a wrong `source_id`/`url`, making misattribution representable. Code
  attachment makes it unrepresentable.
- **Concurrent per-chunk extraction + list reducer now.** Rejected for v1: it is
  the speculative abstraction ADR 0008 avoided; the reducer earns its complexity
  only with the checkpointer's durable partial state.
- **Deterministic (non-LLM) extraction (regex/NLP).** Rejected: deciding what a
  passage *claims* and how strongly it supports it is judgment (CLAUDE.md §4),
  not a deterministic transform — that is precisely what an agent is for.
- **Keep threading individual kwargs (defer `ResearchDeps`).** Rejected: the M6
  ADR pre-registered the trigger and it fired at four collaborators; deferring
  further would just grow the kwarg list it was meant to cap.

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (`Evidence` schema +
  inline-provenance pattern), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral), [ADR 0003](0003-model-router-llm-fabric.md) (model role
  fabric), [ADR 0004](0004-node-dependency-injection.md) (factory-closure DI),
  [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by
  `extract`), [ADR 0006](0006-source-discovery-and-search-fabric.md) (typed
  provenance + §11 structural precedent), [ADR 0008 §Negative](0008-source-ingestion-and-fetch-fabric.md)
  (the `ResearchDeps` trigger this ADR honors).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent vs tool), §5.5 (Reasoning band), §11
  (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M7 (this), M8 (Cross-Verification).
