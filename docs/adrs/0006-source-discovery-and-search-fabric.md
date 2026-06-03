# ADR 0006: Source Discovery and the Search Fabric

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Tech Lead, Council (discovery-as-agent / tool-first / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M5 opens the Knowledge Acquisition band: turn the Research Planner's
prioritized `SubQuestion`s into discovered `Source`s. CLAUDE.md §5.6 names a
"Source Discovery Agent," and ROADMAP M5 says "search/query planning."

The build sandbox has **no network**, so real web search is unavailable — the
same constraint that batched the LLM provider adapter into M-LP (ADR 0003). The
council debated whether M5 is buildable offline at all. The decisive question
was provenance integrity (CLAUDE.md §11, which names "no distinction between
evidence and inference" as a bad pattern): **may an LLM author a `Source`?**

## Decision

**Split discovery into an agent (judgment) and a tool (retrieval), and forbid
the LLM from ever minting a `Source`.**

1. **`SourceDiscoveryAgent` (judgment, `agents/source_discovery.py`).** Given the
   plan, it asks the model fabric (`PLANNING` role) for search *queries* and
   source *types* — genuine judgment (reformulation, type selection), legitimately
   LLM-authored because a query is an *intent*, not a provenance claim. The model
   returns an internal `_DiscoveryOutput` DTO (queries only — never URLs, ids, or
   timestamps).
2. **`SearchProvider` tool + `FakeSearchProvider` (`services/search/`).** A
   provider-neutral protocol (`search(query) -> list[SearchResult]`) mirroring the
   M2 `ModelProvider` fabric. The provider — never the LLM — produces the `url`
   that becomes a `Source`. The concrete network adapter (Tavily/Brave/etc.) is
   deferred to M-LP behind the protocol (exactly the ADR 0003 deferral pattern);
   M5 ships the protocol + the hermetic fake.
3. **The agent holds the provider** (the canonical "agent uses a tool" pattern):
   `SourceDiscoveryAgent(router, search_provider).discover(plan) -> list[Source]`
   plans queries, runs each through the provider, and promotes results to
   `Source`s (ids/timestamps schema-minted). Wired into the `acquire` node via
   factory-closure DI (ADR 0004); `run_research(state, *, planner, discovery)`.
4. **`acquire` stays a single node.** It loops queries and writes
   `acquisition.sources` in a *single* channel write, so no concurrent same-channel
   writes occur and the fan-out reducer decision stays deferred to M7 (ADR 0002 §6).
   This is §6's own "single aggregator writes the channel once" option.

### Schema: `Source.discovered_via: str` (typed, required)

A `Source` gains a required `discovered_via` field (e.g. `"search:fake"`,
`"search:tavily"`), **symmetric with `Evidence.extracted_via: str`** (verified to
exist and be typed `str`). It is the machine-readable encoding of §11's
evidence-vs-inference distinction: a `Source` is always tool-discovered.

**Consistency with M4 (explicit reconciliation).** The M5 council initially
proposed storing discovery provenance in the existing `raw_metadata: dict[str,
str]`. We **reject** that here, because it contradicts the M4 precedent (ADR 0005),
where `error` was made a *typed* field precisely because untyped dicts are "where
provenance rots." The discriminator from M4 applies: is this *load-bearing
provenance* or incidental metadata? The discovery origin is load-bearing — it is
the §11 evidence chain the entire engine's value rests on — so it earns a typed,
required field, consistent with `extracted_via`. Incidental detail (the specific
query string that surfaced a source) goes in `raw_metadata["query"]`. The
per-sub-question attribution link is deferred until a consumer reads it.

This touches the `Source` schema (ADR 0001 territory); it is a small, symmetry-
justified amendment recorded here.

## Consequences

### Positive

- **Provenance integrity is structural, not aspirational.** An LLM cannot author
  a `Source.url`; only the search tool can. Fabricated URLs cannot enter the
  evidence chain (which `Evidence` snapshots inline, ADR 0001) — the §3.2 /
  NotebookLM-style source-grounding promise is protected by construction.
- The search fabric mirrors the LLM fabric (protocol + fake now, real adapter →
  M-LP), a twice-blessed pattern (ADR 0003), and is fully hermetically testable.
- `acquire` is now a real end-to-end band, offline-verified.

### Negative

- **Offline verifies wiring/mapping/contract, not discovery *quality*.** The
  `FakeProvider`/`FakeSearchProvider` cannot tell us the real model proposes good
  queries or the real backend returns good sources — the same ceiling M3 has.
  Quality is an M-LP/eval concern.
- **`discovered_via` is a required field with no default** — a breaking change to
  `Source`. Mitigated by updating every construction site (the M1 placeholder is
  removed; the schema test + JSON fixture updated) and `extra='forbid'` + tests
  catching omissions.
- A `Source` produced by `FakeSearchProvider` has a fixture URL — honest (it is a
  test fixture, not an LLM hallucination), but not a *real* fetched source until
  the M-LP adapter lands.

### Neutral

- `SearchQuery`/`_DiscoveryOutput` are transient agent-output DTOs (co-located
  with the agent, like `_PlannerOutput`), not persisted to `ResearchState`.
- M5 reuses `ModelRole.PLANNING` (no new `DISCOVERY` role) — smallest diff; a
  dedicated role is a future split if a distinct tier/cost consumer appears.

## Deferred (with reasons)

- **Real `SearchProvider` adapter** (Tavily/Brave/direct fetch) → M-LP
  (network-gated), behind the protocol + `@pytest.mark.integration`.
- **A search *router* over multiple backends** — unlike CLAUDE.md §6's explicit
  multi-*model* routing mandate, there is no multi-search-backend mandate, so a
  router now would be speculative (§7/§13). Ship the protocol; add a router with
  its second backend.
- **Per-sub-question attribution** (a typed `sub_question_id` link on `Source`) —
  no consumer reads it yet; the query string is captured in `raw_metadata`.
- **Concurrent discovery** (`asyncio.gather` over queries) — a network-latency
  optimization with no offline benefit; its only correct topology appears with
  real adapters (M-LP), and it interacts with the fan-out reducer (M7).
- **Fan-out reducer / parallel acquisition** → M7 (ADR 0002 §6).

## Alternatives considered

### Option A — Agent emits `Source`s directly

The discovery agent's model returns `Source`s. **Pros:** simplest; one component.
**Cons:** the model would author `url`/`title` — fabricated **inference
masquerading as evidence**, the exact §11 anti-pattern; offline those URLs are
hallucinated and poison every downstream artifact via `Evidence`'s inline
provenance snapshot. **Why rejected:** it corrupts the evidence chain at its root.

### Option B — Discovery provenance in `raw_metadata`

Store `discovered_via` as a `raw_metadata` key. **Pros:** no schema change.
**Cons:** contradicts the M4 precedent (typed `error` over untyped dict for
load-bearing data); untyped/optional provenance is itself the §11 anti-pattern.
**Why rejected:** discovery origin is load-bearing provenance and deserves the
typed, required treatment `extracted_via` already gets.

### Option C — Defer M5 as "too network-blocked"

**Pros:** avoids fake-only work. **Cons:** false premise — the agent (judgment)
and the fabric (protocol + fake + wiring) are genuinely buildable and valuable
offline, mirroring M2. **Why rejected:** M5 is meaty offline, not thin.

## References

- Related: [ADR 0001](0001-research-state-and-provenance.md) (`Source`,
  `Evidence.extracted_via`, provenance pattern), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral), [ADR 0003](0003-model-router-llm-fabric.md) (fabric +
  fake, adapter deferred to M-LP), [ADR 0004](0004-node-dependency-injection.md)
  (factory-closure DI), [ADR 0005](0005-workflow-error-handling.md) (typed
  lifecycle field precedent; failure wrapper the acquire node inherits).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §5.5/§5.6, §7/§13, §11
  (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M5 (this), M6 (ingestion), M7 (extraction
  + fan-out), M-LP (real providers).
