# ADR 0037: Topic / Trend Sourcing Layer

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

The pipeline needs a *front door*: where do fresh, high-potential short-form
video topics come from? Today the Deep Research engine takes a topic as input —
a human supplies it. The feature backlog flags **trend-awareness** as a wanted
capability, and CLAUDE.md §3.4 reserves "long-term topic memory" and analytics
feedback as future layers added via ADR. This ADR opens a **topic / trend
sourcing layer** (`backend/app/topics/`) as that front door.

Per CLAUDE.md §4 the work splits cleanly along the agent-vs-tool line, and both
halves here are **tools**, not agents:

- **Sourcing** trending topics is API-wrapping — deterministic execution. It is
  the structural twin of source discovery (ADR 0006): an LLM must never *invent*
  a trend as if it were an observed fact (§11).
- **De-dupe + ranking** is a deterministic transformation over candidates. There
  is no judgment in it — it applies one explainable signal. The actual
  *green-light* decision ("which surfaced topic do we make a video about") is a
  judgment that belongs to a future content-strategy agent (§5.6's Short-Form
  Content Strategist) that consumes this layer's prioritized output.

Constraints mirror the search/LLM adapters (ADR 0007/0013/0021): the sandbox has
HTTP egress but no `pip` index, and `httpx` is already a runtime dependency. So
the live adapter is `httpx`-based and fully offline-verifiable via
`httpx.MockTransport`; the live call is a `@pytest.mark.integration` smoke test.

## Decision

**Ship a standalone `backend/app/topics/` package: a `TrendProvider` protocol +
`TopicIdea` DTO, a hermetic `FakeTrendProvider`, an `httpx` live adapter
(`HttpTrendProvider`), and a deterministic `select_topics` ranking tool.**

- **Seam.** `TrendProvider` is an async `Protocol`
  (`discover(*, niche, limit) -> list[TopicIdea]`), mirroring `SearchProvider`.
  Async matches the rest of the fabric (real sourcing is network I/O). The
  `FakeTrendProvider` is a factory-style fake (scripted ideas, per-niche mapping,
  call recording) — "don't mock what you can fake."
- **`TopicIdea` is `Source`-shaped, deliberately *not* `SearchResult`-shaped.**
  This is the one place the layer diverges from the search fabric, so it is
  called out explicitly. `SearchResult` keeps provenance *off* the thin DTO and
  mints it only when a hit is promoted to a persisted `Source`. A `TopicIdea` is
  *itself* the persisted artifact handed to the scheduler, so — like `Source` —
  it carries an **auto-minted id** (`topic_…`) and a **required `sourced_via`**
  set by the provider (`"trends:fake"`, `"trends:trends"`), symmetric with
  `Source.discovered_via`. That `sourced_via` is the §11 anchor: a candidate
  topic is always tool-discovered, never authored by an LLM.
- **Ranking signal (the load-bearing decision).** `TopicIdea.signal: float | None`
  is a provider-authored, higher-is-hotter score (e.g. normalized search
  volume/growth). `select_topics` sorts by `signal` **descending**, with an
  explicit secondary tie-break (normalized title, then id) so ordering never
  depends on input order or a stable-sort accident (which would flake tests).
  `None` (provider reported no signal) ranks **lowest**.
- **De-dupe.** Ideas are collapsed by a normalized key (lowercased,
  whitespace-collapsed `keyword`, falling back to `title`). On collision the
  **highest-signal idea is kept wholesale** (its id, its `sourced_via`).
  Sources/scores are deliberately **not merged** — that would overbuild "simple"
  and blur provenance.
- **Output.** `select_topics` is a pure function returning a new ordered
  `list[TopicIdea]` (optionally capped by `limit`), ready for the scheduler's
  topic queue. It never mutates input. The scheduler itself is **not** built —
  "ready for the queue" describes the output's purpose, not an integration.
- **Live wire contract.** `GET {base}/trends`, `X-Api-Key` header, params
  `{q, limit}`, returning `{"trends": [{keyword, score, url?, title?}]}` — a
  deliberately *generic* shape so any trends/keyword API can sit behind it (§6).
  Absent/empty `trends` is a valid empty result; only a mistyped payload is
  wrapped in a locally-defined `TrendError`. Operational failures (429/timeout/
  5xx via `raise_for_status`) propagate as `httpx` errors. The key never leaks
  into a repr/log/error.

### Scope discipline

- **No `Settings` field.** The brave/tavily adapters added a config key, but
  that touches `config.py`, which is out of this layer's ownership. The
  integration test reads `REEL_AUTOMATION_TRENDS_API_KEY` directly from the
  environment and skips when absent. A `Settings.trends_api_key` +
  factory/router wiring is a deliberate follow-up at the composition root.
- **Local `_gen_id` copy.** Like the media layer (ADR 0019), `topics/` keeps a
  local copy of the ADR 0001 id scheme rather than importing from `app.schemas`,
  so this §3.4 layer (independent of Deep Research) builds/tests/showcases
  standalone.
- **One generic live adapter, not a vendor-specific one.** A concrete Google
  Trends / keyword-SaaS adapter is config/wiring, not new code.

## Consequences

### Positive

- The pipeline has a real front door: niche/seed → ranked candidate topics, with
  provenance intact end-to-end.
- The §11 boundary holds at the new layer's head (provider mints `sourced_via`)
  and the ranking is fully explainable and deterministic — no LLM in the loop.
- Fully offline-verifiable: request building, mapping, empty-vs-malformed, the
  ranking order, `None`-signal handling, and de-dupe-keep-highest are all unit
  tested.

### Negative

- **Live payload unverifiable offline.** The generic wire shape is a reasonable
  default pinned by the hermetic fixtures; a real vendor may differ and need a
  thin mapping tweak (confirmable only via the integration smoke).
- **No wiring / scheduler yet.** Selection returns a list; nothing consumes it
  into a persisted queue. That (and a `Settings` field + provider router) is the
  natural next step.

### Neutral

- New package, no change to existing modules. No new dependency (`httpx` +
  Pydantic are already runtime).

## Deferred (with reasons)

- **Scheduler / topic queue persistence** — the consumer of this output; a
  separate component once a store is chosen.
- **`Settings.trends_api_key` + factory/provider router** — config layer is out
  of scope here; follows once a composition root needs it.
- **Content-strategy *agent* that green-lights a topic** — the judgment half
  (§5.6); this ADR ships only the deterministic tools it will consume.
- **Cross-provider signal normalization** — `select_topics` compares signals
  within one candidate set; multi-provider fan-out with differing scales earns
  normalization when a real second provider lands.

## Alternatives considered

### Option A — Make `TopicIdea` thin like `SearchResult` (no id/provenance)

**Pros:** symmetry with the search fabric. **Cons:** a `TopicIdea` *is* the
persisted artifact (it goes to the scheduler queue), unlike a `SearchResult`
that is later promoted to a `Source`. Without inline provenance the §11 anchor
would have to be re-minted downstream. **Why rejected:** the artifact's lifecycle
matches `Source`, not `SearchResult`; it should carry id + provenance like one.

### Option B — Model selection/ranking as an agent

**Pros:** richer "which topic is best" reasoning. **Cons:** ranking by an
explicit signal is deterministic and explainable — making it an agent violates
§4 ("if a task is primarily deterministic, do not model it as an agent"). **Why
rejected:** the judgment lives one layer up (the strategy agent that *consumes*
this ordered list); the tool stays a pure transform.

## References

- Related: [ADR 0006](0006-source-discovery-and-search-fabric.md) (the
  agent-vs-tool discovery split this mirrors), [ADR 0019](0019-media-production-layer.md)
  (the standalone-layer + local-`_gen_id` precedent), [ADR 0001](0001-research-state-and-provenance.md)
  (the id scheme + attached-provenance rationale `TopicIdea` follows),
  [ADR 0021](0021-brave-search-adapter.md) (the httpx + `MockTransport` adapter
  pattern, operational-vs-shape error boundary, key-never-leaks posture).
- [CLAUDE.md](../../CLAUDE.md) §3.4 (future layers via ADR), §4 (agent-vs-tool),
  §6 (provider-neutral, policy-driven), §7/§13 (no speculative overbuild), §11
  (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Topic / Trend Sourcing.
