# ADR 0040: Durable SQLite job store (M13, durable backend)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0031 shipped the async job surface (`POST /research/jobs` + `GET
/research/jobs/{id}`) backed by an **in-memory** `JobStore`. That ADR named its
own limitation explicitly: the store is *single-process and non-durable* — "a
restart loses all jobs" — and deferred "a durable, shared-state store (Redis / a
database)" to a later milestone.

This ADR delivers the **durable** half of that deferral: a SQLite-backed store
that persists jobs so an enqueued or completed job is still readable after the
process restarts. It deliberately does **not** attempt the *cross-worker* half
(shared state across processes) — that remains a later concern.

## Decision

### 1. `SqliteJobStore` — same interface, same lifecycle, durable storage

A new `backend/app/services/jobs/sqlite_store.py` adds `SqliteJobStore`,
interface- and lifecycle-identical to the in-memory `JobStore`:

- `enqueue(topic)` mints a fresh `ResearchState` (`QUEUED`) and persists it.
- `run(job_id, runner)` transitions the job to `RUNNING`, awaits the
  workflow-agnostic `JobRunner`, and persists the terminal state verbatim — so
  the terminal `status` mirrors the workflow's own (which may itself be `FAILED`
  in-band). An *uncaught* runner exception is converted into a `FAILED` snapshot
  (mirroring the workflow's `_with_failure_handling`), so a background failure is
  observable via `get` rather than lost to the event loop.
- `get(job_id)` returns the current snapshot or `None` (the transport-agnostic
  not-found signal the router maps to 404).

Reusing the exact contract is what keeps the API unchanged when the backend is
later swapped at the composition root.

### 2. The persisted value is the canonical `ResearchState` JSON

The stored value is the `ResearchState` itself, serialized via
`ResearchState.model_dump_json` and restored via `model_validate_json` — the same
"job record *is* the canonical state" choice ADR 0031 §2 made, now applied to
durable storage. The round-trip preserves aware-UTC timestamps and the nested
band substates, so a deserialized job is structurally equal to the one persisted.

The table is one row per job: `jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL)`.
Status and error live **inside** the JSON (mirrored verbatim from the workflow),
deliberately **not** duplicated as columns. A parallel `status` column would be a
second source of truth that could drift from the workflow's own — the exact
duplication ADR 0031 §2 rejected. Schema is `CREATE TABLE IF NOT EXISTS`, so the
same path reopens cleanly across restarts.

### 3. A `JobStoreBackend` protocol — a separate protocol, not a rename

The task asked for "a small `JobStore` Protocol the in-memory and sqlite backends
both satisfy." A literal rename is impossible: the composition root
(`app.main.create_app`, out of scope here) instantiates the in-memory store as
`JobStore()`, and a `typing.Protocol` **cannot be instantiated**. So:

- the concrete in-memory class **keeps its name `JobStore`** and its behavior
  unchanged (`deps.py`/`research.py` keep annotating concrete `JobStore`,
  verbatim — fully backward-compatible);
- a distinctly-named `JobStoreBackend` protocol (`base.py`,
  `@runtime_checkable`) is the *structural* seam both backends already meet.

New code that wants to be backend-agnostic annotates `JobStoreBackend`; nothing is
forced to migrate. The protocol covers only the three lifecycle methods the router
uses — backend-specific affordances (`SqliteJobStore.close`) stay out of it.

### 4. Single connection, single lock, single loop

One long-lived `sqlite3.Connection` is opened in `__init__` and all access is
serialized by one `asyncio.Lock`, with **synchronous** SQLite calls inside. Every
method runs on the event-loop thread (including `run`, scheduled as a FastAPI
background task), so the connection's default `check_same_thread=True` guard is
satisfied **without** offloading. The lock is released across `await runner` (held
only around the DB reads/writes), so a long-running job does not block other
access — mirroring the in-memory store's lock discipline exactly.

A `:memory:` database is **per-connection** and so cannot persist across
instances; durability requires a file path (the production use). `:memory:`
remains the cheap default for a bare instance.

### 5. Capability only — not yet wired

This PR adds the backend and its protocol; it does **not** change the `app.state`
default (the in-memory `JobStore` stays the default). Adopting `SqliteJobStore` is
a one-line composition-root change in a later PR — out of scope here per the
ownership boundary (`main.py`/`config.py`/`api/` untouched).

## Consequences

**Positive.** Jobs survive a process restart — the in-memory store's named
limitation is now optionally addressable by swapping the backend. The two stores
are interchangeable behind `JobStoreBackend` with the API unchanged. Stdlib
`sqlite3` only — no new dependency.

**Negative / deferred.** Still **single-process**: one connection in one process,
so a job enqueued on one worker is invisible to another — a cross-worker/shared-
state store (Postgres/Redis) remains deferred. SQLite calls briefly block the
event loop (acceptable at this scope; the `asyncio.to_thread`/connection-per-call
offload is deferred). Not yet wired as the default. Streaming progress and
`CANCELLED` remain open M13 / Research-Control items (carried from ADR 0031).

**Risks.** None to the running API — the change is additive (a new backend file +
a protocol + a re-export); it does not touch `research_state.py`, the composition
root, the router, or the in-memory store's behavior.
