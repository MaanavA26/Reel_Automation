# ADR 0044: Provider client lifecycle — build once, close on shutdown

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

A resource-leak audit (HIGH) flagged the API's provider wiring. The FastAPI
dependencies `get_research_deps` and `get_video_pipeline` called the composition
root (`build_research_deps` / `build_video_pipeline`) on **every request**. Each
build constructs fresh provider adapters, and every httpx-backed adapter
(`HttpxFetchProvider`, the LLM adapters `OpenAICompatibleProvider`/
`GeminiProvider`, the search adapters `TavilySearchProvider`/`BraveSearchProvider`,
`HttpTtsProvider`, `StockVisualProvider`, and the publishing/analytics/topics
adapters) creates its own `httpx.AsyncClient`. Those clients were **never
closed**, and there was **no FastAPI `lifespan`/shutdown** hook. Every `/research`
and `/videos` call therefore leaked the underlying sockets / file descriptors.

A related low-severity follow-up from ADR 0043: three adapters
(`publishing/youtube.py`, `analytics/youtube.py`, `topics/live.py`) still
interpolated the full upstream response body (`{data!r}`) into error messages —
the same info-leak the §43 audit fixed elsewhere but explicitly left as a
documented follow-up.

Two correctness constraints had to be preserved:

1. **The test seam.** `tests/api/test_research.py` / `test_videos.py` override
   `get_research_deps` / `get_video_pipeline` via `app.dependency_overrides` with
   fake-backed bundles. The fix must not break that override path — the singleton
   is the *default*, overridable path.
2. **Lazy `CompositionError`.** An unconfigured app must still *boot* and return
   503 per request (the composition root raises when no model/search backend is
   wired). Building the providers eagerly at startup would crash an otherwise
   bootable app and break `test_default_composition_root_fails_loud`.

## Decision

**1. An `aclose()` lifecycle contract on every client-owning adapter.**
`app/core/lifecycle.py` defines a minimal `AsyncClosable` protocol (`aclose()`)
and a `CloseOwnedClientMixin` that adds `aclose()` + `async with` from a
`self._client` + `self._owns_client` pair. Each adapter sets
`self._owns_client = client is None` so it closes **only a client it created** —
an *injected* client (the `httpx.MockTransport` test seam, or a shared client) is
the caller's to close. Adding the mixin is a one-line change per adapter.

**2. The composition root returns the closables alongside the deps.** The agents
wrap their providers privately, so the composition root — the only place that
knows which concrete adapters were minted — returns a small frozen bundle
(`ResearchBundle`/`MediaBundle`/`VideoPipelineBundle`) carrying the built deps
**plus** the httpx-owning seams as `tuple[AsyncClosable, ...]`. This avoids the
alternative of reaching through agent internals (`agent._router._providers`) to
find the clients, which would break the agent/tool encapsulation (CLAUDE.md
§4/§10) and be fragile to refactors.

**3. App-scoped lazy singletons on `app.state`.** `get_research_deps(request)` /
`get_video_pipeline(request)` now take the `Request` (mirroring `get_job_store`),
check `app.state` for a cached bundle, and build it **on first use** if absent —
caching the deps and appending the bundle's closables to `app.state.aclosables`.
The build stays lazy, so `CompositionError` still surfaces per request as 503 and
an unconfigured app still boots. One build per app replaces one build per request.

**4. A FastAPI `lifespan` that drains on shutdown.** `create_app` registers a
`lifespan` whose startup is intentionally empty (providers are built lazily) and
whose shutdown closes every `AsyncClosable` registered on `app.state.aclosables`,
then closes the job stores' backends **if they expose `close()`** (only the
`SqliteJobStore` does; the in-memory default is a no-op). Close failures are
logged, never raised, so one bad client cannot block the rest of shutdown.

**5. The info-leak follow-up.** `publishing/youtube.py`, `analytics/youtube.py`,
and `topics/live.py` now clip their upstream-body excerpts to a bounded
`_ERR_BODY_MAX` prefix, reusing the sibling adapters' `repr(data)[:N]` idiom.

## Consequences

### Positive
- The socket/FD leak is closed: clients are built once per app and released on
  shutdown.
- The agent/tool boundary is preserved — the API layer never introspects agent
  internals; the composition root owns provider identity *and* lifecycle.
- The test override seam and the lazy-`CompositionError`/503 contract are intact
  (verified: `tests/api/` passes unchanged except for the deliberate `(deps,
  closables)` unwrap in the composition unit tests).
- The CLI (`make_video`) also closes its clients in a `finally` block.

### Negative / trade-offs
- `build_research_deps` / `build_media_deps` / `build_video_pipeline` return a
  bundle now, not the bare deps — a small ripple touching their three callers
  (`deps.py`, `pipeline.py`, the composition unit tests).
- The singleton is process-local (one per `create_app()`), consistent with the
  in-memory `JobStore` model; a multi-worker deployment builds one set of clients
  per worker (acceptable — that is httpx's intended pooling unit).
- A partial-build leak remains in one narrow case: if the model client builds but
  search then raises `CompositionError`, the model client is orphaned on that one
  failing request. The common unconfigured case raises on the model first
  (nothing built), so it is not exercised; full partial-build cleanup is a
  documented, low-value follow-up.

## Alternatives considered
- **Reach into agents to find clients** (`agent._router._providers`). Rejected:
  breaks encapsulation (CLAUDE.md §4/§10) and is fragile.
- **Build eagerly in `create_app` like `JobStore()`.** Rejected: `JobStore()`
  never fails, but the composition root raises when unconfigured — eager build
  would crash startup and break the boot-then-503 contract.
- **A per-request `async with` over freshly-built deps.** Rejected: still
  rebuilds (and re-pools) on every request — it closes the leak but discards
  connection reuse, the opposite of httpx's design.

## References
- ADR 0031 (async job store, app.state singleton model), ADR 0040 (SqliteJobStore
  `.close()`), ADR 0032 (composition root / video pipeline), ADR 0043 (the
  `{data!r}` info-leak idiom this finishes).
- CLAUDE.md §4 (agents vs tools), §9 (scope discipline), §10 (thin routers /
  layered boundaries).
