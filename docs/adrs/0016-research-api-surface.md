# ADR 0016: Deep Research API surface (M13, partial)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (sync-vs-async / response-shape / composition-root architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Deep Research pipeline now runs end-to-end in-process (`run_research`,
M3-M10b): plan → acquire → ingest → extract → verify → synthesize → critique →
publish, with a bounded revision loop. It has had no HTTP surface — it is only
reachable from tests and the `python -m app.cli.plan` harness. M13 ("API + job
submission + frontend wiring") opens that surface.

This PR delivers the **first, bounded slice** of M13: a thin FastAPI router to
submit a research job and read its result, plus the composition root that
assembles the workflow's `ResearchDeps` from `Settings`. Streaming progress,
background execution, an id-addressable status endpoint, and frontend wiring are
explicitly out of scope and remain on M13.

The council surfaced four decisions.

## Decision

### 1. Synchronous v1 — `await run_research`, return the terminal state

`POST /research` runs the job to completion in the request and returns the final
typed `ResearchState`. In a synchronous model the response **is** the result, so
it satisfies both halves of "submit and read result/status" with one endpoint:
`status` + the band substates are the job result.

Background execution, streaming, and a job store are deferred. They are a
materially different concern (durable state, cross-worker addressability,
progress transport) and shipping them speculatively before a consumer (the
frontend) exists would violate CLAUDE.md §7/§13. The synchronous path is a
correct, demonstrable v1 — not a stub.

**Deferred (M13 async slice):** "read status/result" by **id** —
`GET /research/{id}` and the job store that backs it. An in-memory dict was
explicitly rejected: it is cross-worker-broken and adds no v1 value over the
synchronous terminal response.

### 2. Response shape — the canonical `ResearchState`, returned verbatim

The response model is `ResearchState` itself, not a bespoke API DTO. It is
already strict (`extra='forbid'`), fully typed, and carries the complete result
(plan, sources, evidence, verdicts, synthesis, critiques, status, error). A
parallel API DTO would duplicate the schema and drift. The request is a small,
API-local `ResearchJobRequest` (`topic` + a bounded `max_syntheses`,
`ge=1, le=10`, so a caller cannot force an oversized revision cycle), defined
**inline in the router** rather than in `schemas/` — it is an HTTP-edge contract,
and keeping it in the router avoids a `schemas → workflows` import inversion
(it reuses the workflow's `DEFAULT_MAX_SYNTHESES`).

### 3. Composition root — a pure wiring service, fail-loud at the seam

`build_research_deps(settings) -> ResearchDeps` lives in
`app.services.composition` with **no FastAPI import**, mirroring
`llm.factory.build_router_from_settings` for the model fabric. The thin,
overridable `Depends` provider (`get_research_deps`) lives in `app.api.deps` and
just calls into the service. This keeps the router thin (CLAUDE.md §10) and the
wiring testable in isolation.

Two collaborators are **honest holes** at this stage and are surfaced as a loud
`CompositionError`, never silently stubbed:

- **Search.** No production `SearchProvider` exists (only `FakeSearchProvider`;
  the live adapter is network-gated, M-LP.2). Building the discovery agent
  raises with a message pointing at M-LP.
- **Model provider.** With the default `default_provider`,
  `build_router_from_settings` already raises (no adapter registered); the
  composition root normalizes that `ValueError` into `CompositionError` so every
  wiring failure surfaces through **one** type — and one HTTP status.

Shipping a `Fake*` as a production default was rejected: it would leak test
doubles into a running service and silently mask the missing adapter.
Construction is **lazy** (per request, inside the dependency fn, never at
import), so the app boots before any adapter is wired and tests override the
provider before the first request.

### 4. Registration + error mapping

The research router is aggregated in `app/api/router.py` (alongside `health`),
the established pattern — `main.py` never names individual sub-routers. `main.py`
is touched **only** to register a `CompositionError → 503` exception handler:
wiring/config gaps are service-unavailable, not bad requests or bare 500s, so
the cause stays legible to a caller.

## Consequences

**Positive.** The pipeline is reachable over HTTP; the composition root is the
one place production collaborators are assembled from config; tests inject a
fully fake-backed `ResearchDeps` via `app.dependency_overrides` and exercise the
real workflow end-to-end with zero network (hermetic). The router is thin —
validate, delegate, return.

**Negative / deferred.** A long research run blocks its request for its full
duration (acceptable for v1 / a demo; the async slice fixes it). There is no
id-addressable status read and no progress transport until the job store lands.
The real `_build_search_provider` raise is currently unreachable in tests (the
model-provider `ValueError → CompositionError` path fires first when no LLM
provider is configured); coverage of that exact branch waits for a configured
provider.

**Risks.** None new to the pipeline — this PR adds only the surface and wiring;
it does not touch `research_state.py`, `deep_research.py`, or any agent/service
logic.
