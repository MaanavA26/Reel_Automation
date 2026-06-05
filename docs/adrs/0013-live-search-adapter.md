# ADR 0013: First Search Provider Adapter (Tavily, httpx)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (Tavily / Brave / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

M5 (ADR 0006) shipped the provider-neutral search fabric — the `SearchProvider`
protocol, `SearchResult` DTO, and a hermetic `FakeSearchProvider` — and
**deferred the concrete network adapter** to M-LP because the build sandbox has
no network (the same deferral M2/ADR 0003 made for the LLM, filled by M-LP.1/ADR
0007). The Source Discovery agent (M5) therefore plans real queries but
retrieves only faked URLs. To unblock discovery end-to-end, one concrete
`SearchProvider` must land.

Constraints (identical to ADR 0007): the sandbox has no network and cannot
install provider SDKs, but **`httpx` is installed** (already a runtime
dependency). The user's runtime has network + a key.

## Decision

**Ship one Tavily adapter built on `httpx`: `TavilySearchProvider`
(`services/search/live.py`, `name = "tavily"`).**

- **Tavily over Brave.** Tavily is purpose-built for LLM/research agents and is
  the *closer mirror to the M-LP.1 LLM adapter*: a single `POST /search` with a
  JSON body and `Authorization: Bearer` auth — the same request shape as
  `OpenAICompatibleProvider` — versus Brave's `GET` + `X-Subscription-Token`.
  It also returns a content snippet per hit, mapping cleanly onto
  `SearchResult.snippet`. Both have free, no-card tiers; either is defensible,
  and the protocol means swapping (or adding Brave) later is a new module behind
  the same interface, not a reversal.
- **Mapping.** `results[].url → url`, `title → title`, `content → snippet`;
  `source_type` is always `SourceType.WEB` (a web-search API returns web pages —
  we do **not** infer PDF/paper/youtube from the URL, which would be speculative
  and unrequested). A result missing a `url` is skipped (it cannot become a
  `Source`). Returns at most `limit` results, mirroring `FakeSearchProvider`.
- **Config.** `Settings` gains `search_api_key: SecretStr` (its own field, *not*
  the LLM's `api_key`/`base_url`) so search and the model are configured
  independently; a Tavily block is added to `.env.example`. `httpx` is already a
  runtime dependency; no new dependency.

### Error boundary (mirrors ADR 0007)

*Operational* failures (429 rate-limit, timeout, 5xx) propagate as raised
`httpx` errors — retries/budgets/failover are the **Orchestrator's** job
(deferred per ADR 0003/0005) and are not swallowed here. Only an unparseable
response *shape* is wrapped in `SearchError`. The API key never appears in an
error message or repr.

### Hardening (mirrors ADR 0007, not ADR 0008)

Bounded timeout + a key that never leaks. The SSRF caps (size/redirect/
content-type) from the ingestion fetcher (ADR 0008) are intentionally **omitted**
— those guard attacker-influenced URLs; this adapter calls a single *trusted*
API endpoint.

## Consequences

### Positive

- **Source Discovery is runnable end-to-end against a real backend** with just a
  free Tavily key — closing the M-LP.1 gap (the Planner runs live; discovery
  retrieved faked URLs). The provenance promise of ADR 0006 now holds against
  *real* URLs: the tool — never the LLM — mints them.
- Mirrors the twice-blessed adapter pattern (ADR 0003/0007): protocol + fake +
  one httpx adapter, fully offline-verifiable via `httpx.MockTransport`; only the
  live call needs network (`@pytest.mark.integration`, skipped without a key).
- Provider choice stays a config concern behind the protocol — no lock-in.

### Negative

- **Live response *quality* is unverifiable offline** (whether real results are
  good for our queries) — the same ceiling as the LLM adapter; an eval concern.
  The *wire contract*, however, was confirmed against the live Tavily docs:
  `POST https://api.tavily.com/search`, `Authorization: Bearer <key>`, body
  `query`/`max_results`/`search_depth:"basic"`, and `results[]` items carrying
  `url`/`title`/`content` — so the hermetic mapping tests assert the real shape,
  not a guess.

### Neutral

- **No wiring.** Per scope (M-LP.2 is the adapter only), this ADR adds no
  composition root / factory and touches no agent or the graph — Source
  Discovery still receives its provider via the existing M5 DI seam. Wiring the
  live provider in by config is a trivial fast-follow.

## Alternatives considered

### Option B — Brave Search API

**Pros:** independent index (not reselling Google/Bing); generous free tier.
**Cons:** `GET` + `X-Subscription-Token` diverges from the M-LP.1 `POST` +
`Bearer` shape, so the two adapters mirror each other less; results carry a
shorter `description` than Tavily's `content`. **Why not first:** Tavily is the
better research-engine fit and the closer mirror; Brave can be added behind the
same protocol if a second backend is wanted.

### Option C — Direct web fetch + scrape (no search API)

**Pros:** no third-party key. **Cons:** there is no query→URL discovery step
without an index; scraping a search engine's results page is brittle and
ToS-fraught. **Why rejected:** a purpose-built search API is the correct tool.

## Deferred

- **A search *router* over multiple backends** — no multi-search-backend mandate
  exists (unlike CLAUDE.md §6's multi-*model* mandate), so a router now is
  speculative (ADR 0006 already defers this). Add it with its second backend.
- **Wiring the live provider into Source Discovery / a composition root** — the
  next milestone; this ships the adapter behind the protocol.
- **Provider-SDK adapters / Brave** — fast-follow behind the same protocol.

## References

- Related: [ADR 0006](0006-source-discovery-and-search-fabric.md) (the search
  fabric this adapter fills; "real adapter → M-LP"), [ADR 0007](0007-openai-compatible-llm-adapter.md)
  (the LLM-adapter pattern this mirrors: httpx + MockTransport + error boundary),
  [ADR 0008](0008-source-ingestion-and-fetch-fabric.md) (the fetch hardening this
  deliberately does *not* replicate for a trusted endpoint).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6, §7/§13, §11 (evidence vs
  inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP.2 (this), M5 (the discovery agent it
  unblocks), M-LP.1 (the LLM adapter it mirrors).
- [`backend/.env.example`](../../backend/.env.example) — run recipe.
