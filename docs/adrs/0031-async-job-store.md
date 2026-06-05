# ADR 0031: Async job store + status endpoints (M13, async slice)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0016 shipped the first M13 slice: a **synchronous** `POST /api/v1/research`
that awaits `run_research` and returns the terminal `ResearchState`. It blocks
the request for the run's full duration — acceptable for a v1 demo, but it leaves
two M13 items open: background execution and an id-addressable status read.

This ADR delivers that **async slice**: enqueue a job and return immediately,
run the workflow in the background, and read status/result by id.

### Relationship to ADR 0016 (the in-memory-store rejection)

ADR 0016 *explicitly rejected* an in-memory dict job store, calling it
"cross-worker-broken and adds no v1 value over the synchronous terminal
response." That rejection was scoped to the **synchronous** slice — there, the
response already *is* the result, so a store added a moving part for no behavior.
The async slice changes that calculus: enqueue must return *before* the result
exists, so a place to hold the in-flight job and address it by id is now load-
bearing, not redundant. An in-memory store is therefore the right **bounded**
next step; the cross-worker/durability objection from 0016 still stands and is
carried forward here as an explicit deferral, not a solved problem. The
synchronous endpoint is **kept** unchanged alongside the new async surface.

## Decision

### 1. New endpoints, the sync endpoint preserved

- `POST /api/v1/research/jobs` — enqueue. Returns **202** with the `QUEUED`
  `ResearchState` (carrying the id to poll). Schedules `run_research` as a
  FastAPI **background task**.
- `GET /api/v1/research/jobs/{job_id}` — read the current snapshot; **404** on an
  unknown id.

The path is `/research/jobs/{id}` (per the task), not 0016's speculative
`/research/{id}` — the `jobs` segment keeps the async lifecycle surface distinct
from the sync submit endpoint at `/research`.

### 2. The job record is the canonical `ResearchState`, not an envelope

`ResearchState` already carries an opaque `id` (preserved end-to-end through the
graph's partial-update contract), a `status`
(`QUEUED`/`RUNNING`/`COMPLETED`/`FAILED`), and an `error`. Reusing it as the
stored value — the job id **is** `state.id` — avoids a duplicate status field that
could drift from the workflow's own, keeps this service entirely out of
`schemas/` (owned elsewhere), and means the terminal `status` mirrors the
workflow's verbatim (which may itself be `FAILED` in-band, e.g. an exhausted
node). The terminal snapshot therefore serves both status polling and result
reads from one payload.

### 3. `JobStore` service owns the lifecycle; the router stays thin

All bookkeeping — mint `QUEUED`, transition to `RUNNING`, invoke the runner,
record the terminal state — lives in `app.services.jobs.JobStore` (CLAUDE.md
§4/§10). The router only enqueues, schedules, and reads-or-404s. The store takes
a `JobRunner` callable (`ResearchState -> Awaitable[ResearchState]`) rather than
importing `run_research`, so the orchestration stays workflow-agnostic and
trivially fakeable. A runner exception is converted to a `FAILED` snapshot inside
`run` (mirroring the workflow's own `_with_failure_handling`), so a background
failure is observable via `GET` rather than lost to the event loop.

### 4. Process-singleton on `app.state`; `deps` resolved at the request edge

The store is **stateful** and must be one instance per process — enqueue and read
have to hit the same dict. It is created once in `create_app`
(`application.state.job_store = JobStore()`) and read off `request.app.state` by
the `get_job_store` provider, never rebuilt per request. (`get_research_deps`
stays per-call: it is stateless.) Each `create_app()` gets its own store, giving
tests per-app isolation with no override.

`deps` is resolved via `Depends(get_research_deps)` in the POST handler and
**closed over** by the background task — never rebuilt inside the task, where
`app.dependency_overrides` would not reach it. This keeps the async path as
hermetically testable as the sync one.

### 5. Concurrency — one lock, by design

Everything runs on a single event loop, so dict ops are effectively atomic; a
single `asyncio.Lock` guards mutations for honesty and future-proofing rather
than to tame real contention. No heavier machinery is warranted at this scope.

## Consequences

**Positive.** Long runs no longer block their request; a client gets an id
immediately and polls. The pipeline now has both a sync (one-shot) and an async
(enqueue/poll) HTTP surface. The store is a clean Research-Control-band service
(CLAUDE.md §5.5 A — "job lifecycle / progress tracking") with a typed,
workflow-agnostic seam. Tests drive enqueue → poll → completed and unknown-id →
404 fully hermetically (`BackgroundTasks` complete before the POST returns under
`TestClient`, so no sleeps).

**Negative / deferred.** The store is **single-process and non-durable**: a
restart loses all jobs, and a job enqueued on one worker is invisible to another.
A durable, shared-state store (Redis / a database) is deferred. Streaming
progress (vs. poll), cancellation (`CANCELLED`), retries, and budgets remain
open M13 / Research-Control items. There is no intermediate `RUNNING` observation
under `TestClient` (the task runs to completion synchronously there) — it is
observable only under a real ASGI server.

**Risks.** None to the pipeline — this PR adds only the async surface, a new
`services/jobs/` service, and the `app.state` wiring; it does not touch
`research_state.py`, `deep_research.py`, the composition root, or any
agent/service logic.
