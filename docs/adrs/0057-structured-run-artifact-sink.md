# ADR 0057: Structured, DB-ready per-run artifact + log sink

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

A research/video run produces a deeply structured `ResearchState` (plan → sources
→ evidence → verdicts → findings → report → creator packet) and, on the video
path, a `MediaPlan` (narration script + render metadata). Today that output lives
only in the job store as one nested `ResearchState` blob (ADR 0040 §2: "the state
*is* the record") or is thrown away after a render. Issue #114 asks for the output
to be persisted **locally now, in a structured, DB-portable shape**, so it can
later be bulk-loaded into a free structured database — and for the persisted form
to be flat rows with stable keys and UTC ISO timestamps, not a single nested
document.

The nested blob is the right choice for the job store (one row, re-hydrate the
whole workflow) and the wrong shape for a relational/columnar target, which wants
flat rows keyed by a run id. So this needs a *projection* + a *sink*, not a reuse
of the job store.

This is **storage and serialization** — deterministic and procedural — a
**tool/service**, never an agent (CLAUDE.md §4). Every reasoning step already
happened upstream in the research agents; this layer only flattens and writes.

The structured app logging the issue also names already exists: `app.core.logging`
(ADR 0030) emits one JSON object per line with a UTC `ts` and the active `run_id`
(ADR 0030 / `run_context`). The deliverable is therefore the **artifact sink**,
with at most a thin helper layered on the existing logger — not a new logging
stack.

## Decision

### 1. A pure projection layer — one flat record per artifact kind

`backend/app/services/runlog/records.py` defines one strict (`extra="forbid"`)
Pydantic model per artifact *kind* (`RunRecord`, `SourceRecord`, `EvidenceRecord`,
`VerdictRecord`, `FindingRecord`, `ReportRecord`, `ReportSectionRecord`,
`CreatorPacketRecord`, `HookRecord`, `AngleRecord`, `NarrativeRecord`,
`MediaRecord`) plus a pure projector function from a `ResearchState` / `MediaPlan`
to each kind's row list. Every record carries the owning `run_id` — the future
foreign key a downstream loader uses to map kind → table and `run_id` → FK — and
lifts the most query-useful fields to the top level while retaining the original
artifact ids for re-join to the full provenance chain.

The records deliberately do **not** re-snapshot the provenance graph (a
`VerdictRecord` keeps its evidence ids as a list, not the embedded `Evidence`).
The graph already lives in the canonical state the sink persists alongside the
records; the records are the query-friendly *view*.

Serialization is `model_dump(mode="json")`. That single choice satisfies three
requirements for free: aware-UTC datetimes render as ISO-8601 strings, enums
flatten to their string values, and the field names are stable scalar keys — no
hand-rolled timestamp formatting.

### 2. A `RunArtifactSink` protocol + `FileRunArtifactSink` — the durable-backend seam

`backend/app/services/runlog/sink.py` defines a `@runtime_checkable`
`RunArtifactSink` `Protocol` with a single async method
`write(state, *, media_plan=None)`, mirroring the `ChannelStore` (ADR 0042) /
`JobStoreBackend` (ADR 0040) idiom: a future durable backend (a
`SqlRunArtifactSink` or a columnar loader) implements the same contract and drops
in without touching callers. `FileRunArtifactSink` is the production default. It
writes, under `<base_dir>/<run_id>/`:

- `run.json` — the single run-header record (one object, not a one-element array);
- one `<kind>.json` per artifact kind — a JSON array of flat records;
- `media.json` — the narration script + render metadata, **only** when a
  `MediaPlan` is supplied;
- `state.json` — the canonical `ResearchState` for lossless re-hydration
  (the ADR 0040 §2 round-trip discipline, kept alongside the query view).

One file per kind (rather than a single bundle) keeps the basename = future table
name, so the on-disk layout maps one-to-one onto a DB schema. Empty bands write an
empty array (`[]`) — an explicit "this band produced nothing" row set, symmetric
with ADR 0001's empty-substate convention — so the kind file always exists and a
loader's table set stays stable.

The output `base_dir` is **injected** (the caller passes the gitignored
`backend/runs/` root; tests pass `tmp_path`), keeping the sink pure and
relocatable and the tests hermetic.

### 3. Async seam, synchronous IO inside, deterministic writes

`write` is **async** so the seam survives a future I/O-bound DB backend unchanged
(the `ChannelStore` rationale: "the deferred durable backend would be async"). The
file writes inside are **synchronous** — the repo's established
sync-IO-inside-async stance (ADR 0040 §4). The sink is idempotent per run id (each
file is overwritten, never appended), so a retried persist replaces rather than
duplicates.

Crucially, the sink mints **no** write-time timestamps into the records — every
timestamp is the source artifact's own aware-UTC value. Two writes of the same
state therefore produce byte-identical files (the only non-determinism, the random
artifact ids, lives on the state, not the sink), which is what makes the
round-trip and idempotency tests exact rather than approximate.

### 4. A metadata-only per-stage event helper, via one additive formatter seam

`backend/app/services/runlog/events.py` adds `log_stage_event(stage, *, metrics)`,
a thin convenience over the existing JSON logger (ADR 0030) — it does **not**
reinvent the formatter or the run-id correlation. The formatter already stamps the
UTC `ts` and the active `run_id`; this helper attaches a small structured payload
(`stage` + numeric `metrics`) so a stage transition is queryable in shipped logs.

Carrying that payload required one **additive** change to `JsonFormatter`: it now
recognizes a single reserved `event` record attribute and emits it under an
`event` key; a record without it is byte-identical to before. This is the minimal
formatter touch (ADR 0030 is otherwise unchanged), and it is the honest one — a
helper that silently dropped its payload (the formatter emits a fixed key set)
would pass a "does it run?" test while shipping nothing structured.

Info-leak discipline (ADR 0043): the records sink persists the research *bodies*
(it writes to a gitignored dir, so that is in-contract); the app logger must not.
`metrics` is typed `Mapping[str, int | float]`, so a claim, narration, url, or
`state.error` string cannot be passed without a type error — the exact leak vector
ADR 0043 closed, kept closed.

### 5. Capability only — not yet wired

This ADR adds the projection, the sink, and the event helper; it does **not**
force the sink into `VideoPipeline` or `run_research`. The documented integration
point is the closed-loop runner / pipeline tail (ADR 0054), where a
`FileRunArtifactSink(settings.runs_dir)` would `await sink.write(state,
media_plan=plan)` after a terminal run — a small, clean follow-up. This mirrors the
"capability now, wiring later" precedent of ADR 0040 §5 and keeps this change's
blast radius to new files + one additive formatter line.

## Consequences

**Positive.** A run's full output is persistable in a flat, DB-portable shape with
stable keys and UTC ISO timestamps — ready to bulk-load into a free structured DB
when one is chosen. The `RunArtifactSink` seam means that DB backend is a drop-in
behind the same `Protocol`, exactly as the durable job/channel stores are deferred.
The records and the canonical `state.json` coexist: query the flat view, re-hydrate
the full schema. The event helper makes stage transitions queryable in shipped logs
without a new logging stack. Stdlib + Pydantic only — no new dependency.

**Negative / deferred.** Not yet wired into the live pipeline (capability only).
The file sink is single-process and non-durable in the DB sense (it is files on
disk); a real DB backend remains deferred. The flat records are a lossy *view* (no
embedded provenance graph) — lossless re-hydration goes through `state.json`, not
the records. The descriptor-level media record carries uris/metadata, not the
audio/video bytes (those live where the media layer wrote them).

**Risks.** None to the running system — the change is additive: new files under
`app/services/runlog/`, a `backend/runs/` gitignore line, and one reserved-key
branch in `JsonFormatter` that leaves existing log lines byte-identical. No
schema, router, composition root, or pipeline behavior changes.

## Alternatives considered

- **Dump `ResearchState.model_dump_json()` verbatim per run.** Rejected: that is
  the job store's nested-blob choice (ADR 0040 §2), the wrong shape for a
  structured DB — the issue explicitly asks for *flat* records with stable keys.
  (The canonical blob is still persisted as `state.json` for re-hydration, which
  keeps the lossless path without making it the primary record shape.)

- **A single bundle file per run instead of one file per kind.** Acceptable per
  the issue ("one file per artifact kind, or a single bundle — your call"), but
  one-file-per-kind makes basename = table name, so the on-disk layout maps
  directly onto a DB schema and a bulk loader needs no unpacking step.

- **Extend the logger to do the artifact persistence (a "log sink" that also
  writes bodies).** Rejected: that conflates two concerns and would route research
  bodies through the app logger — the precise ADR 0043 leak. The split is the
  point: the gitignored *records* hold the bodies; the *logger* carries
  numbers-only stage events.

- **Skip the event helper entirely / fold metrics into the message string.**
  Rejected: the issue names a "log sink", and folding metrics into the message is
  text, not structured/queryable data. The single additive `event` formatter seam
  is the smallest change that makes the helper actually structured; deferring it
  would have shipped a broken-but-passing helper, the one outcome to avoid.

- **A `FakeRunArtifactSink` test double (the `FakeChannelStore` analog).**
  Rejected as overbuild (CLAUDE.md §7): `FileRunArtifactSink` over `tmp_path`
  covers all four required test pillars hermetically; a second implementation with
  no consumer would be speculative.
