# ADR 0054: Closed-loop automation runner

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0034 shipped three pure scheduler primitives — `Schedule` (clock-free
next-run math), `TopicQueue` (FIFO/priority backlog), `BatchRunner` (concurrent,
error-isolated produce-one-video) — and **explicitly deferred the long-lived
driver loop** that composes them: the process that actually waits on the cadence,
drains a batch, produces videos, and (now) gates, publishes, and feeds outcomes
back. That deferral named exactly what was missing: the only piece touching a real
clock and real sleeping, and the site where the injected `Produce` binds to the
real `VideoPipeline` (and the publishing step).

Since then the surrounding seams have all landed: the end-to-end `VideoPipeline`
(ADR 0032), the deterministic `PrePublishGate` (ADR 0041) whose REVIEW rung says
"do not auto-publish, route to a human", the human-review gate `ReviewService`
(ADR 0051) — which left "gate publish on `APPROVED`" as a documented follow-up —
the publishing fabric (ADR 0033), topic sourcing/selection (ADR 0037), the
analytics feedback scorer (ADR 0036), and budget guardrails (ADR 0035). Every
component to close the unattended N-videos-per-day loop exists; nothing wires them.

This is **orchestration/scheduling**, which is deterministic and procedural —
a **tool/service**, never an agent (CLAUDE.md §4). The judgment already happened
upstream: topic selection + the strategist agents decide *what* to make; the
safety gate's policy and the human's sign-off decide *whether* to post. The loop
only *sequences* those decisions.

## Decision

Add a `ClosedLoopRunner` (`backend/app/scheduler/closed_loop.py`) — a deterministic
tool that composes the existing primitives + real seams into the unattended loop,
realizing the driver ADR 0034 reserved. Wire it from the composition root
(`build_closed_loop`) and drive it from a CLI (`python -m app.cli.run_loop`).

### 1. `run_once` (no clock) split from `run_forever` (the one timing seam)

Mirroring ADR 0034's pure-vs-timing ethos: **all** behavior lives in
`async def run_once()` which contains zero sleeping — publish-approved, source,
enqueue, drain, produce, gate, act, record. `run_forever` is the thin wrapper:
`while not stopped: wait until schedule.next_run_after(now()); await run_once()`.
The clock (`now`) and sleeper (`sleep`) are **injected** (the budget tracker /
eval-harness convention), so the entire loop is tested with a fake clock + a
sleeper that advances it — zero real waiting. The composition root is the one
place that binds `datetime.now(UTC)` + `asyncio.sleep`.

### 2. Two modes — only the ALLOW branch differs (config-selected)

`LoopMode.SUPERVISED` (default, safe) vs `LoopMode.AUTONOMOUS` (opt-in). BLOCK
always drops; REVIEW always holds for a human — identical in both modes. Only an
ALLOW verdict diverges:

- **supervised**: even ALLOW is routed through the review gate (`submit` →
  `PENDING_REVIEW`); **nothing auto-posts**. A human approves via the existing
  reviews API; a later tick posts it.
- **autonomous**: ALLOW + within budget → publish immediately; only REVIEW holds.

`loop_mode` defaults to `supervised`, and `loop_privacy_status` defaults to
`private`, so unattended posting is doubly opt-in.

### 3. The flat→rich seam gap, and the additive `create_bundle`

`VideoPipeline.create` returns a *flattened* `VideoArtifact` (ids + uri), but the
gate needs the `Report` + `CreatorPacket` + a candidate, and the publisher needs
the `RenderedVideo`. Rather than re-derive those (which means re-running
research), `VideoPipeline` gains an **additive** `create_bundle` returning a
`ProducedVideo` bundle `(artifact, research_state, report, packet, media_plan)`;
`create` now delegates to it and projects down (public return unchanged). The
`Report` is resolved by `packet.report_id` (not the last published report), so the
gate evaluates the exact report the packet was built from. `Produce`'s `T` is this
bundle, so `BatchRunner`'s error isolation is reused unchanged.

### 4. approved→publish: a pending-publication stash + a runner method

A `ReviewRecord` carries only `subject_id`/`subject_label` — **no payload** — so an
approval cannot reconstruct the `RenderedVideo`/`PublishTarget`. At submit-time the
loop stashes the publishable bundle in a new process-local `PendingPublicationStore`
keyed by `subject_id`; `publish_approved()` scans the review gate for `APPROVED`
records, publishes any still in the store, and pops on success (idempotent — no
re-publish next tick). It is a **runner method** (run at the top of `run_once`,
also callable out-of-band) rather than an API endpoint, so the publish network I/O
stays out of a request handler and the reviews router stays thin (CLAUDE.md §10).
We deliberately do **not** extend `ReviewRecord` (it stays a pure human-decision
record) and do **not** re-run the pipeline on approval.

### 5. Budget — the loop owns a coarse ceiling

`BudgetTracker` is LLM-**spend**-scoped (a model-provider decorator) and is not
even wired into the live router, so there is no per-video loop budget today.
Rather than rebuild accounting, the loop owns its own `BudgetTracker` with a flat
per-video estimate, charged **before** producing and (in autonomous mode) before
publishing. A breach raises `BudgetExceededError`, which `BatchRunner` captures
into the per-topic result (a skipped topic, logged — never a silent cap, never a
crash); an autonomous publish that would breach is held for review instead.

### 6. Error isolation + graceful shutdown

Per-topic produce failures reuse `BatchRunner`'s isolation. A publish failure is
captured, logged, and counted — never crashes the loop; an approved-publish failure
re-stashes the hold for retry. A `run_once_guarded` wrapper captures an unexpected
loop-body defect so `run_forever` outlives a single bad tick. A `StopSignal` sets a
cooperative flag **and** wakes the inter-tick wait (via `asyncio.wait_for` over an
event), so a shutdown finishes the in-flight tick and exits without blocking for a
multi-hour sleep.

### 7. Analytics feedback steers topic priority (cross-tick)

Stats lag (a just-posted video has no views), so feedback is necessarily cross-tick:
publish records `topic↔post_id`; a later tick fetches stats for known posts,
`score_topics`, and maps a higher score to a *lower* enqueue priority so a proven
topic jumps the queue. Analytics is optional (`None` disables it; the loop still
produces/publishes). No stats scheduler is built.

## Consequences

### Positive

- The unattended N/day loop is **real and runnable** end-to-end, closing the
  deferral ADR 0034 reserved, while the three primitives stay pure.
- `run_once`/`run_forever` split keeps nearly all behavior hermetically testable
  with no clock; the injected clock/sleeper gate only the thin wrapper.
- Strict agent-vs-tool separation held (CLAUDE.md §4): the loop is pure procedure.
- The flat→rich `create_bundle` is additive — the API's `VideoArtifact` contract
  and `create` are unchanged.

### Negative

- **Autonomous mode auto-posts to real platforms.** It is the last-mile,
  live-key-gated income loop and defaults OFF (`loop_mode=supervised`,
  `loop_privacy_status=private`); enabling it is a deliberate operator opt-in with
  real publishing consequences.
- `PendingPublicationStore` (and `TopicQueue`, `ReviewService`) are single-process
  and **non-durable**: a restart loses pending holds and the backlog (the standing
  ADR 0031/0051 objection). A durable backend is deferred.
- The loop budget is a coarse per-video count/cost ceiling, not a true cost
  invoice — distinct from (and additional to) the model-fabric per-call budget.
- **Backlog growth.** Each tick enqueues *all* sourced topics but drains only
  `batch_size`, and `select_topics` de-dupes only *within* a call — so a topic that
  keeps trending re-enqueues across ticks and the `TopicQueue` can grow unbounded
  over a long run. Acceptable for the single-process, non-durable v1 (a restart
  clears it); a cross-tick de-dupe / enqueue cap is a deferred follow-up.

### Neutral

- `build_closed_loop` imports `services.video.pipeline` lazily to break the
  module-load cycle (`composition → closed_loop → video.pipeline → composition`);
  the composition root assembling the loop is the correct layering direction.
- The cadence is an `IntervalSchedule` (default 6h) in the CLI; a `DailySchedule`
  is a drop-in alternative (both satisfy `Schedule`).

## Alternatives considered

### Option A — extend `ReviewRecord` to carry the publishable payload

Embed the `RenderedVideo`/`PublishTarget` on the record so approval can publish
directly. Rejected: it bloats a pure human-decision record (ADR 0051's deliberate
"reference by id, not payload" design) and couples the review band to the media
band. The separate pending-publication stash keeps the review record clean.

### Option B — approved→publish as an API endpoint

A `POST /reviews/{id}/publish` that uploads on demand. Rejected: it puts slow
network upload I/O inside a request handler (against the thin-router rule) and
splits the publish path across two surfaces. A runner method polled in `run_once`
(also callable out-of-band) keeps one publish path and the router thin.

### Option C — `Schedule` owns the clock / the loop sleeps inline

Rejected for the same reason ADR 0034 rejected it: fusing timing math to real
waiting makes the loop untestable without sleeping. Keeping `run_once` clock-free
and injecting the clock/sleeper into `run_forever` is what makes the loop hermetic.

## References

- Realizes the deferral in ADR 0034 (scheduler — the three primitives + deferred
  driver loop). Builds on ADR 0051 (human-review gate — the `APPROVED`→publish
  follow-up this realizes) and ADR 0041 (pre-publish safety gate — ALLOW/REVIEW/
  BLOCK). Consumes ADR 0032 (video pipeline), 0033 (publishing), 0036 (analytics
  feedback), 0035 (budget), 0037 (topic sourcing/selection).
- CLAUDE.md §3.4 (orchestration/automation fabric), §4 (agents vs tools), §7
  (hermetic testability), §10 (thin routers/CLI).
