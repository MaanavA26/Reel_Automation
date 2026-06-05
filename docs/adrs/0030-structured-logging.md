# ADR 0030: Structured logging + run-tracing scaffold

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

Modules across the codebase already log through `logging.getLogger(__name__)`
(ingestion service, the five agents). What is missing is (a) a *single place*
that configures the root logger so those lines land somewhere consistent and
machine-parseable, and (b) **correlation**: a Deep Research job fans out across
many nodes/agents (plan → acquire → ingest → extract → reason → publish), and an
operator triaging one run needs every line that run emitted, regardless of which
module logged it. Without a run id stamped on each line, logs from concurrent or
interleaved runs are indistinguishable.

This is a deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM), and the
quality bar (§7 "clear logging") and conventions (§10) apply. The build sandbox
cannot `pip install`, so the solution must be **pure stdlib** — no `structlog`,
no `python-json-logger`.

## Decision

**Ship two new core modules, stdlib-only, and leave wiring to the entrypoint.**

- **`app/core/run_context.py`** — a module-private `ContextVar[str | None]`
  carrying the active `run_id`, exposed via `get_run_id()`, `bind_run_id()` /
  `reset_run_id()`, and a `run_context(run_id)` context manager that resets in a
  `finally` (so an exception or nested run cannot leak a stale id). `contextvars`
  is async- and thread-safe by construction, so concurrent runs never bleed ids.
- **`app/core/logging.py`** — `JsonFormatter` renders each `LogRecord` as one
  single-line JSON object (`ts` ISO-8601 UTC, `level`, `logger`, `message`,
  `run_id`, plus `exc_info`/`stack_info` when present), reading the `run_id` from
  the context var **at format time** so no logging call site needs to know about
  it and existing `getLogger(__name__)` callers gain correlation for free.
  `setup_logging(level, *, json=True)` configures the root logger idempotently
  (clears handlers first → no duplicate lines on re-call), writing to `stdout`.
- **`run_id` is always present (`null` when unbound)** — a stable schema is
  friendlier to downstream log parsing than a sometimes-missing key.
- **No import-time side effect.** Importing the module does not reconfigure the
  root logger. The app entrypoint calls `setup_logging()` once at startup; this
  ADR does **not** edit `main.py` (owned by another agent) — the API maintainer
  wires the one-line call.

## Consequences

### Positive

- One JSON object per line → log shippers / `jq` parse output directly.
- Any Deep Research code path that wraps work in `run_context(run_id)` gets every
  emitted line correlated, with zero changes to existing logging call sites.
- Pure stdlib: no new dependency, works in the offline sandbox.
- Fully hermetic to verify: the formatter is tested in isolation from a
  constructed `LogRecord`, and the contextvar binding end-to-end via a `StringIO`
  handler — no reliance on mutating global root-logger state.

### Negative

- `setup_logging` mutates the *global* root logger (inherent to stdlib logging);
  callers that want isolation must manage their own handlers. Mitigated by
  idempotency and by tests that save/restore root state.
- The `json=False` path uses a printf-style `Formatter`, which cannot read the
  context var directly, so it needs a small `_RunIdFilter` to stamp `run_id` onto
  each record. Minor duplication of the "fetch run_id" concern across the two
  formatting paths.

### Neutral

- ADR number 0030 is ahead of the current sequence (0021) by design — this is an
  isolated component branch; the gap is cosmetic and renumbering is not required.
- No schema change, no config change, no new dependency.

## Alternatives considered

### Option A — `structlog` / `python-json-logger`

**Pros:** batteries-included structured logging, processor pipelines. **Cons:**
new dependency, and the sandbox has no PyPI index, so it could not be installed
or verified here. **Why rejected:** CLAUDE.md §10 (stdlib-only for this scope)
and the offline constraint; the stdlib `Formatter` + `ContextVar` covers the
requirement (one JSON line + run correlation) in ~120 lines.

### Option B — Thread the `run_id` through function signatures / a logger adapter

**Pros:** explicit, no global state. **Cons:** every node and every helper would
have to accept and forward a `run_id`, and existing `getLogger(__name__)` callers
would all need rewriting. **Why rejected:** `contextvars` gives the same
correlation transparently and async-safely without touching call sites — exactly
why the stdlib added it.

### Option C — Omit `run_id` when unbound (sometimes-missing key)

**Pros:** marginally smaller lines. **Cons:** unstable schema; every downstream
parser has to handle "key may be absent." **Why rejected:** a fixed schema with
`run_id: null` is the friendlier contract.

## References

- Related: [ADR 0001](0001-research-state-and-provenance.md) (`run_id` is the
  Deep Research job identity this correlates on), [ADR 0005](0005-workflow-error-handling.md)
  (the orchestrator lifecycle whose progress/triage this logging supports).
- [CLAUDE.md](../../CLAUDE.md) §3.1 (observability), §4 (tool, not agent), §7
  (clear logging), §10 (stdlib, typed, modular).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Ops / Infrastructure (observability scaffold).
