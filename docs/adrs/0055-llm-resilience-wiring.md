# ADR 0055: LLM resilience wiring (retry gate in the composition root)

- **Status:** Accepted
- **Date:** 2026-06-12
- **Deciders:** Tech Lead
- **Supersedes:** none (realizes the wiring ADR 0027 deferred)
- **Superseded by:** none

## Context

The first **live** `topic → video` run (the last mile, CLAUDE.md §13; issue
#107) failed at cross-verification: Groq's free tier gives each model a
per-minute token budget (measured 2026-06: `llama-3.3-70b-versatile` **12K
TPM**, `llama-4-scout` **30K**, `gpt-oss-120b` **8K**, `llama-3.1-8b-instant`
**6K**), and with `PLANNING`/`EXTRACTION`/`LONG_CONTEXT` all pointed at the
same model, extraction drained the shared bucket and all ~170 verification
calls 429'd inside one rate window. Zero verdicts → `VerificationError` → the
pipeline correctly refused to render.

The cure already existed: `services/llm/resilience.py` (ADR 0027) ships
`ResilientModelProvider` — bounded retry-with-backoff — but as **capability
only**; ADR 0005/0027 deferred the wiring (and the transient-vs-permanent
narrowing) "to the future wiring site", and ROADMAP M4 deferred retries to
**M-LP (need live providers)**. The live run is M-LP surfacing that deferral.

One gap blocked a faithful wiring: `RetryConfig.retry_on` narrows by exception
*type*, but `httpx.HTTPStatusError` is one type carrying both transient (429,
5xx) and permanent (401, 404) failures. A type tuple cannot express "retry the
429s, fail the 401s loud".

## Decision

### 1. `RetryConfig.retry_if` — instance-level narrowing (resilience.py)

An optional predicate `retry_if: Callable[[Exception], bool] | None` refining
`retry_on` *instances*: a caught transient-typed error for which the predicate
returns `False` propagates immediately (no retry, no backoff). `None` (default)
preserves the existing type-only behavior. Also: `name` becomes a plain
mirrored attribute (the `CachingModelProvider` precedent) so the decorator
satisfies the `ModelProvider` protocol's settable `name: str`.

### 2. The wiring gate (`_build_router`, composition root)

When `Settings.llm_retry_max_attempts > 1`, the composed provider is registered
**wrapped** in `ResilientModelProvider` with
`retry_on=(httpx.HTTPStatusError, httpx.TransportError)` and the wiring-site
predicate `_is_transient_llm_error` (429 or ≥500 or transport fault — never
auth/config 4xx, which would only delay the loud failure the composition root
promises). The **inner** adapter is still what's returned for lifecycle close
(ADR 0044) — the decorator owns no httpx client. Default
`llm_retry_max_attempts = 1` disables the wrap entirely: hermetic behavior and
every existing test are unchanged (capability-off-by-default, the ADR 0035
pattern).

Settings knobs (`.env`): `LLM_RETRY_MAX_ATTEMPTS`, `LLM_RETRY_BASE_DELAY`,
`LLM_RETRY_BACKOFF_FACTOR`, `LLM_RETRY_MAX_DELAY` — delay defaults (5s × 2.0,
cap 60s) sized so a retry ladder spans a free-tier per-minute window reset.

### 3. Per-role rate-bucket split (config guidance, not code)

`.env.example` now ships role models on **separate Groq buckets** — planning/
verification on `llama-3.3-70b-versatile` (12K), extraction on
`llama-3.1-8b-instant` (own 6K), long-context synthesis/report/packet on
`meta-llama/llama-4-scout-17b-16e-instruct` (30K — a large synthesis prompt
must fit its bucket or it can *never* succeed) — so one band cannot starve
another. Free-tier limits are per-model; the split multiplies effective
throughput with zero code change (CLAUDE.md §6 policy-driven routing).

## Consequences

- Live runs survive 429 bursts by backing off across rate windows instead of
  failing every call in one window; permanent errors stay loud and immediate.
- Hermetic suite untouched (retry off by default); new behavior covered by
  focused tests (predicate semantics, wiring gate on/off, narrowing table).
- The router-level fallback hop (`ResilientRouter`, the other ADR 0027 half)
  stays **unwired** — agents call `router.for_role` directly; wiring fallback
  is a separate decision when the Orchestrator owns failure judgment (ADR 0005).
- Estimated-spend note: retries multiply call volume; budget guardrails
  (ADR 0035) meter per call, so ceilings still bound retried spend.
