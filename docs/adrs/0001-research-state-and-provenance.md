# ADR 0001: Research State and Provenance

- **Status:** Accepted
- **Date:** 2026-05-25
- **Deciders:** Tech Lead, Council (advisor + `Maanav's-MacAir`)
- **Supersedes:** none
- **Superseded by:** none

## Context

Phase 0 of the Reel Automation Deep Research engine begins with this PR. Before any agent or tool implementation lands, the data shape that flows between them must be defined. Deep Research is structured as four bands (Research Control, Knowledge Acquisition, Knowledge Reasoning, Research Publishing — see [CLAUDE.md §5.5](../../CLAUDE.md)), and each band produces and consumes structured outputs:

- Sources discovered and ingested;
- Chunks parsed from those sources;
- Evidence extracted from chunks (claim plus lineage);
- Verification outcomes and contradictions;
- Synthesis output, creator-ready packets, and final reports.

Two architectural questions must be settled before substantive code lands, because both are painful to retrofit:

1. **Provenance attachment.** Does each extracted claim carry its lineage inline (a self-contained snapshot of the source and chunk that backs it), or does state hold opaque references into a separate provenance ledger?
2. **State container shape.** One canonical container that flows through the workflow (LangGraph state pattern), or multiple narrower containers per band that get composed at workflow time?

Other adjacent questions also benefit from a single deliberate answer now: ID scheme, datetime semantics, mutability of state objects, and how strictly the schema rejects unknown fields. Settling these now prevents the first three workflow PRs from each independently re-litigating them.

## Decision

### State container

A single canonical `ResearchState` Pydantic model is the container that flows through Deep Research workflows. It carries identity and lifecycle metadata at the top level, plus one sub-state per Deep Research band. This matches the standard LangGraph state pattern: nodes accept the full state and return updated state.

The four band sub-states:

- `acquisition: KnowledgeAcquisitionState` — implemented in this PR.
- `plan: ResearchPlan` — Research Control band, added in a subsequent Phase 0 PR.
- `reasoning: KnowledgeReasoningState` — Knowledge Reasoning band, added in a subsequent Phase 0 PR.
- `publishing: ResearchPublishingState` — Research Publishing band, added in a subsequent Phase 0 PR.

Each band's sub-state defaults to its empty form (e.g., `KnowledgeAcquisitionState()` with empty lists). The *presence* of a sub-state field is not a meaningful signal of "did this band run" — empty state means "no work yet." If a future workflow needs to distinguish "band has not started" from "band ran and produced empty output," a dedicated `band_status` enum is added at that time, not `None` defaults.

### Provenance pattern: attached (inline)

Every `Evidence` object carries a self-contained snapshot of its provenance:

- `source_id` and `source_url` (the source it came from);
- `chunk_id` and `chunk_text` (the specific chunk extracted);
- `confidence` (model-assigned, 0.0–1.0);
- `extracted_at`, `extracted_via` (when and how).

The `source_url` and `chunk_text` are *duplicates* of fields stored on the corresponding `Source` and `Chunk` objects in the same band sub-state. This duplication is deliberate. A state dump can be read end-to-end without joining or traversing the discovery registry — every claim is self-documenting in isolation.

The discovery registry (`sources` and `chunks` lists in `KnowledgeAcquisitionState`) still exists for two reasons:

1. Workflow nodes need to ask "what did we discover so far" without scanning the entire evidence list.
2. Reasoning bands will need to query "what sources support this group of claims" by id.

### Identity scheme

Type-prefixed opaque IDs generated via `secrets.token_hex(8)`. The prefix denotes the object type (`job_`, `src_`, `chk_`, `ev_`); the suffix is 16 lowercase hex characters carrying 64 bits of entropy. Examples:

- `job_a1b2c3d4e5f6a7b8`
- `src_0123456789abcdef`
- `ev_fedcba9876543210`

This balances log readability (the prefix tells a human what kind of object it is) with collision resistance (64 bits is sufficient for an audit-grade system at Phase 0 scale — birthday-collision probability is ~5e-8 at 1M objects per type per job). Hex was chosen over base64url because base64url's `_` and `-` characters would clash with the `_` prefix delimiter: code that ever does `id.split("_")` would corrupt silently on a base64url-encoded suffix. Hex eliminates that footgun.

### Datetime semantics

All timestamps are timezone-aware UTC: `datetime.now(UTC)` (using `from datetime import UTC`, the Python 3.11+ shortcut for `timezone.utc`). The naive-vs-aware mixing bug class is closed by always using the aware form throughout the schema.

**Ordering relations between state objects must derive from band-level workflow logic, not from timestamp comparisons.** Wall-clock UTC works for the common operations (display, search, audit windows) but clock skew across distributed components breaks strict ordering. If a future feature needs cross-object happens-before, an explicit sequence number or workflow-step identifier is added — not a comparison of `created_at` values.

### Mutability

State objects are mutable by default (the Pydantic v2 default; no `frozen=True`). LangGraph node patterns assume nodes can return updated state without manually copying every nested field. Immutable/frozen models would force unnecessary deep-copy boilerplate at every node. If a future replay or audit feature needs immutability, it can wrap state in a frozen snapshot at checkpoint time without changing the runtime model.

### Strict schemas

All models use `ConfigDict(extra="forbid")`. Unknown fields raise `ValidationError` at the parsing boundary. This catches typos and stale-API leakage early; the cost (an explicit allow-list when ingesting external metadata) is paid by dedicated `raw_metadata: dict[str, str]` fields where free-form data legitimately belongs.

## Consequences

### Positive

- State dumps are self-documenting. An `Evidence` shows the reader the claim *and* the chunk it came from, in one object, with no traversal.
- LangGraph integration is the default path; no adapter layer is required between Pydantic models and graph state.
- Strict schemas catch field-name typos and stale-API drift at the JSON boundary, not at runtime hundreds of lines downstream.
- ID prefixes mean grep-ability in logs and explicit type-tagging without runtime overhead.
- 64-bit entropy IDs are large enough that collisions are not a practical concern at Phase 0 scale (see the Identity scheme section for the analysis).

### Negative

- Provenance duplication costs storage and serialization size. A claim that shares a chunk with N other claims sees the chunk's text duplicated N times across `Evidence` objects. Acceptable while research jobs hold thousands (not millions) of evidence items. If that scale shifts, the "referenced (ledger)" alternative in this ADR becomes the upgrade path.
- Mutable state means a bug that updates state outside a workflow node can silently corrupt downstream behavior. Mitigated by code review and, later, state-validation hooks invoked at band transitions.
- `raw_metadata: dict[str, str]` on `Source` is deliberately tight: every value must be a string. The first ingestion PR will likely want to store ints (page counts, retrieval durations), floats (scores), or nested structures (author metadata). Widening this — either to `dict[str, str | int | float | bool | None]` or per-source-type typed metadata models — is a deliberate future decision tracked alongside the ingestion ADR. The tightness is preserved here to keep the first-pass schema honest about what is actually structured vs. what is free-form.

### Neutral

- The "no `None` defaults for band fields" choice means we cannot encode "this band has not started" vs. "this band ran and produced empty output" today. If that distinction becomes operationally meaningful, an explicit `band_status` enum is the response — not retroactively introducing `None` defaults.

## Alternatives considered

### Referenced (ledger) provenance

Each `Evidence` holds only a `provenance_id` pointing into a separate `Ledger[provenance_id] -> Provenance` map. State dumps require joining the ledger to read.

- **Pros.** No duplication. Better for cross-references (one source backs many claims, stored once). Easier audit queries ("which claims came from source S?" is a ledger reverse-lookup, not a state scan).
- **Cons.** Indirection on every read. State dumps unreadable in isolation. Ledger consistency is a separate concern that every workflow node has to honor. Painful debugging when a `provenance_id` resolves to nothing.
- **Why rejected.** Premature for Phase 0 scale. Adopt later (via a superseding ADR) if cross-reference patterns dominate and storage cost matters.

### Truncated UUID for IDs (`uuid4().hex[:12]`)

Initial sketch during scoping — 48 bits of randomness.

- **Why rejected.** Birthday collision probability becomes non-trivial at roughly 10M objects per type per job. For an audit-grade provenance system, "non-trivial collision risk" is the wrong tradeoff. `secrets.token_hex(8)` (the chosen scheme) gives 64 bits in a comparably short 16-character hex string and avoids the prefix-delimiter collision that `secrets.token_urlsafe(12)` would introduce via its base64url alphabet.

### Frozen / immutable state

Use `model_config = ConfigDict(frozen=True)` so state is immutable; nodes construct new state objects rather than mutating.

- **Pros.** Easier replay and audit. The class of bugs where a node mutates state out-of-order disappears.
- **Cons.** Conflicts with the standard LangGraph state-mutation idiom. Forces deep-copy boilerplate in every node. Significant compatibility cost for marginal Phase 0 benefit.
- **Why rejected.** Compatibility with LangGraph patterns wins for Phase 0. Revisit if state-corruption bugs become a real category.

### Multiple top-level state containers

`acquisition_state`, `reasoning_state`, etc., as separate top-level objects composed at workflow time rather than nested inside `ResearchState`.

- **Pros.** Each band's state is independently serializable. Smaller per-band schemas.
- **Cons.** No single "the state of this research job" object. LangGraph integration requires explicit composition at every node boundary. Provenance cross-band references get awkward.
- **Why rejected.** The single-container pattern aligns with the LangGraph default and the operating-contract intent in CLAUDE.md.

## References

- [CLAUDE.md §5](../../CLAUDE.md) — Deep Research architectural intent.
- [`docs/standards/0001-coding-standards.md`](../standards/0001-coding-standards.md) — coding conventions this ADR's implementation conforms to.
- [`docs/standards/0002-testing-standards.md`](../standards/0002-testing-standards.md) — test layout for the schema tests in this PR.
- Future ADR 0002 will document the LangGraph integration that consumes this state shape.
- Future Phase 0 PRs add `ResearchPlan` (Control band), `KnowledgeReasoningState`, and `ResearchPublishingState` substates per the architecture above.
