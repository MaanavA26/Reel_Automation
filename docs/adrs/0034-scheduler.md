# ADR 0034: Scheduler / unattended batch runner

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

The product target is N faceless videos per day produced (and later posted)
**without a human in the loop** — the "performance optimization / orchestration
fabric" automation layer CLAUDE.md §3.4 reserves for a future ADR. That loop has
three concerns: *when* to fire (a cadence), *what* to produce next (a backlog),
and *how* to produce a batch (concurrent execution that survives a bad topic).

The hard design constraint is **determinism without real waiting**. A scheduler
that sleeps on a real clock is untestable except by waiting, and a batch runner
that races on a real event loop is flaky. The repo already answers the timing half
of this elsewhere — `app.eval.harness` injects a `time_fn` clock so latency is
asserted with no real time passing — and the agent sandbox runs offline, so the
whole feature must be verifiable hermetically (CLAUDE.md §7). Scheduling and batch
execution are procedural, not reasoning, so this is a **tool/service** band, never
an agent (CLAUDE.md §4).

## Decision

Add a new `backend/app/scheduler/` package of three deterministic, independently
testable primitives, and **defer the long-lived driver loop + real-pipeline
wiring** as a documented follow-up. The split between the pure primitives and the
loop is the load-bearing decision: it is what makes "no real sleeping in tests"
automatic rather than something to fight.

### 1. `Schedule` is a pure next-run function — no clock, no sleeping

`Schedule.next_run_after(reference: datetime) -> datetime` takes the reference
instant **as an argument** and returns the next fire instant. The schedule stores
no clock; the *caller* owns it (passing `datetime.now(UTC)` in production, a
scripted instant in tests). Two concretes cover the N/day target:

- `IntervalSchedule(interval, anchor)` — slots are `anchor + k*interval`.
  **Anchored**, not naive `reference + interval`, so the fire times are stable and
  reproducible regardless of *when* the loop polls.
- `DailySchedule(times)` — fixed wall-clock times of day, **UTC-only** in v1.

Two correctness rules, both tested at the boundary:
- **Strictly-after semantics.** A `reference` landing exactly on a slot returns the
  *next* slot, never `reference` — so a loop that fires then immediately recomputes
  does not re-fire the same slot.
- **Naive datetimes are rejected.** A naive reference silently producing a wrong
  slot is the classic timezone bug; we fail loud, consistent with the schema's
  tz-aware-UTC convention (ADR 0001).

### 2. `TopicQueue` is an ordered backlog (FIFO + priority) over `heapq`

`enqueue(topic, *, priority=0)` / `dequeue()` / `drain(limit)`. Ordering is a
`(priority, seq, topic)` heap key where `seq` is a monotonic insertion counter.
The `seq` tiebreaker is load-bearing: without it, equal-priority entries fall
through to comparing the topic *strings* — neither FIFO nor meaningful. Lower
`priority` is produced first; ties are FIFO. Process-local and non-durable,
mirroring `JobStore` (ADR 0031).

### 3. `BatchRunner` runs an injected `Produce` callable, capped + isolated

`BatchRunner(produce: Callable[[str], Awaitable[T]], *, max_concurrency)` is
generic over the produce result `T` and **does no timing**. `run_batch(topics)`:

- bounds concurrency with an `asyncio.Semaphore` **created inside `run_batch`**, not
  in `__init__` — a semaphore built at construction binds to whichever event loop
  existed then, breaking tests that `asyncio.run` on a fresh loop;
- isolates per-topic failure: an exception is **captured** into `TopicResult.error`
  (not propagated), so one bad topic never aborts the batch — mirroring `JobStore`'s
  exception-to-terminal-state contract (ADR 0031);
- returns a `BatchResult` whose `results` are in **submission order** (`succeeded` /
  `failed` views derived), so a caller correlates outcomes to inputs deterministically.

The produce step is **injected**, not imported, so the runner is decoupled from the
real `VideoPipeline` (a sibling component) — tests inject a fake. This is the same
workflow-agnostic-seam pattern as `JobStore`'s `JobRunner`.

### 4. The driver loop + real wiring are deferred (scope boundary)

The long-lived process — `while running: sleep until schedule.next_run_after(now());
batch = queue.drain(n); await runner.run_batch(batch)` — is **not** built here. It is
the only piece that touches a real clock and real sleeping, and the site where the
injected `Produce` binds to the real `VideoPipeline` (and later the publishing step).
Keeping it out keeps the three primitives pure and hermetic; it is the follow-up.

## Consequences

### Positive

- The N/day cadence, backlog, and concurrent batch execution exist as three small,
  composable, **fully hermetic** primitives — next-run math, FIFO/priority ordering,
  error isolation, and the concurrency cap are all asserted with **no real waiting**.
- Strict agent-vs-tool separation (CLAUDE.md §4): scheduling/execution stay
  deterministic tools; no reasoning leaks in.
- Decoupled from the real pipeline via the injected `Produce` callable, so the
  sibling `VideoPipeline` can land independently and wire in by injection.

### Negative

- The package is **not yet runnable end-to-end** — there is no driver loop and no
  real pipeline, so it produces nothing on its own until the follow-up.
- `TopicQueue` and the (future) loop are **single-process / non-durable** — a restart
  loses the backlog and any in-flight batch. A durable backlog is deferred (the same
  standing objection as ADR 0031).

### Neutral

- `DailySchedule` is **UTC-only** in v1; civil-timezone + DST handling (a `tz` field)
  is a deliberate follow-up.
- No new dependency — stdlib `asyncio` / `heapq` / `datetime` only (apscheduler /
  celery deliberately avoided to keep the band transparent and offline-testable).

## Alternatives considered

### Option A — `Schedule` owns the clock and sleeps internally

A `Schedule.wait_until_next()` that reads the clock and `await`s. Rejected: it
fuses the pure timing math to real waiting, making it untestable without sleeping
(or pervasive monkeypatching of `asyncio.sleep`/`datetime.now`). Passing the
reference in and lifting sleep to the deferred driver keeps the math a pure function.

### Option B — APScheduler / Celery for scheduling + concurrency

A mature library would give cron parsing and a worker pool. Rejected for this band:
it adds a heavyweight dependency (and a broker for Celery) the offline sandbox can't
install, obscures the showcase logic behind a framework, and overshoots a
single-process N/day loop. Stdlib primitives keep the band transparent, dependency-
free, and hermetically testable; a real queue/worker backend can be revisited if the
scale demands it.

## References

- Related: ADR 0031 (async job store — the `JobRunner` injected-seam + single-process
  non-durable precedent this mirrors); ADR 0001 (tz-aware UTC convention);
  `backend/app/eval/harness.py` (the injected-`time_fn` clock convention).
- CLAUDE.md §3.4 (future automation/orchestration fabric), §4 (agents vs tools),
  §7 (testability quality bar).
