# ADR 0021: Brave Search Provider Adapter (second `SearchProvider`)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0006 split source discovery into an agent (judgment) and a `SearchProvider`
tool (retrieval) so that an LLM can never mint a `Source.url`, and deferred the
concrete network adapter to M-LP. ADR 0013 (sibling branch) lands the first
concrete adapter, `TavilySearchProvider`. This ADR adds a **second** concrete
provider, `BraveSearchProvider`, for failover and robustness.

Two backends is the threshold CLAUDE.md §6 sets for policy-driven routing
("support multiple providers … selected by role / policy"). ADR 0006 explicitly
deferred a *search router* until a second backend existed precisely to avoid
speculative abstraction (§7/§13); this adapter is that second backend. The
router itself remains deferred — this ADR ships only the adapter, behind the
existing protocol, so the two providers are interchangeable by construction.

Constraints are identical to ADR 0007/0013: the sandbox has HTTP egress but no
`pip` index, and `httpx` is already a runtime dependency. So the adapter is built
on `httpx` and is fully offline-verifiable via `httpx.MockTransport`; the live
call is a `@pytest.mark.integration` smoke test, skipped without a key.

## Decision

**Ship one `httpx` adapter over the Brave Web Search API:
`BraveSearchProvider` (`services/search/brave_search.py`, `name = "brave"`).**

- **Wire contract.** `GET https://api.search.brave.com/res/v1/web/search`,
  authenticated with an `X-Subscription-Token` header (plus `Accept:
  application/json`, as Brave is content-negotiation sensitive). Query params
  `q` (the query) and `count` (result count). This differs from Tavily's `POST`
  + `Authorization: Bearer`; each adapter speaks its own API behind the shared
  `SearchProvider` protocol — the point of the protocol.
- **`count` clamp.** Brave returns HTTP 422 for `count > 20`. The protocol's
  `limit` is caller-controlled, so it is clamped to `[1, 20]` before it reaches
  the wire; the post-fetch `[:limit]` slice still honors the caller's intent.
- **Mapping.** `data["web"]["results"][]` → `SearchResult` (always
  `SourceType.WEB` — a web-search API returns web pages; URL-sniffing for
  PDF/paper/YouTube is speculative and unrequested). Brave's snippet field is
  `description` (Tavily's is `content`). A hit with no `url` is skipped — it
  cannot become a `Source`.
- **Empty vs malformed.** An absent `web` block or absent `results` list is "no
  web results for this query" — a valid empty outcome (the repo's "thin result
  is valid" pattern), returning `[]`. Only a *present but mistyped* payload
  (`web` not a dict, `results` not a list, top level not a dict) is wrapped in a
  locally-defined `SearchError`.
- **Error boundary (mirrors ADR 0007/0013).** Operational failures (429,
  timeout, 5xx via `raise_for_status`) propagate as raised `httpx` errors for
  the Orchestrator to own (retries/budgets/failover are its concern); they are
  not swallowed. The key never appears in a log, repr, or error message.
- **Config.** `Settings` gains `brave_api_key: SecretStr`, **distinct** from the
  LLM `api_key` and from any other search provider's key, so search and the
  model are configured independently.

### Scope discipline

`SearchError` is defined locally in `brave_search.py` (mirroring `OpenAICompatError`
in the LLM adapter), **not** added to `base.py`; `__init__.py` is untouched. This
keeps the change to one new module + one additive config field, so it stays
merge-clean with the sibling Tavily branch (which adds its own `live.py` +
`search_api_key` and defines its own `SearchError`). The consequence is that
`BraveSearchProvider` is reachable only via its full import path
(`app.services.search.brave_search`), not the package `__init__` — intended;
factory/router wiring is out of scope.

## Consequences

### Positive

- A real failover search backend exists; the discovery agent can be pointed at
  Brave by construction (config swap once a router lands), honoring §6.
- Provenance integrity holds unchanged — the provider, never the LLM, mints the
  `url` (ADR 0006 / CLAUDE.md §11).
- Fully offline-verifiable: endpoint, auth header, `count` clamp, the
  `description`→snippet mapping, URL-less skipping, and the empty-vs-malformed
  boundary are all unit-tested via `httpx.MockTransport`.

### Negative

- **Live behavior is unverifiable offline.** Whether Brave's current payload
  matches the asserted shape can only be confirmed by the `@pytest.mark.integration`
  smoke test against a real key; the hermetic fixtures pin the contract as
  reconstructed from the API docs.
- **No router yet.** Having two providers but no router means selection is still
  a wiring decision at the composition root, not a runtime policy. The router is
  the natural next step now that the §6 two-backend threshold is met.

### Neutral

- No schema change (`SearchResult`/`Source` already exist). No new dependency
  (`httpx` is already runtime).

## Deferred (with reasons)

- **A search router over multiple backends** — now justified (two backends
  exist) but a separate, focused change; this ADR keeps to the adapter.
- **Factory wiring / `build_*_from_settings` for search** — follows the router.
- **Provider-side knobs** (freshness, country, safesearch, result-type filters)
  — added with a consumer that needs them (§7/§13).

## Alternatives considered

### Option A — Add Brave behind a new shared `SearchError` in `base.py`

**Pros:** one canonical error type. **Cons:** edits a shared file the sibling
Tavily branch also touches, creating an avoidable merge conflict, and the task
scope is one new module + one config field. **Why rejected:** the LLM fabric
already sets the precedent of a per-adapter error class (`OpenAICompatError`);
mirroring it keeps the diff isolated and merge-clean.

### Option B — Infer `source_type` from the URL (e.g. `.pdf` → PDF)

**Pros:** richer typing. **Cons:** speculative, unrequested, and brittle (a
`.pdf` URL is still a web fetch until ingestion proves otherwise). **Why
rejected:** §7/§13 — don't over-generate; a web-search API returns web pages.

## References

- Related: [ADR 0006](0006-source-discovery-and-search-fabric.md) (the search
  fabric this fills; router/second-backend deferral), [ADR 0007](0007-openai-compatible-llm-adapter.md)
  (the httpx + `MockTransport` hardening pattern; operational-vs-shape error
  boundary), [ADR 0013](0013-live-search-adapter.md) (the first concrete
  `SearchProvider`, Tavily, which this mirrors).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (policy-driven routing),
  §7/§13 (no speculative overbuild), §11 (evidence vs inference).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP.2 (concrete search adapters).
