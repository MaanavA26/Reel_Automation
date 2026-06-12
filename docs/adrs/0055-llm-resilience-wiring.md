# ADR 0055: LLM resilience wiring (retry gate in the composition root)

- **Status:** Accepted
- **Date:** 2026-06-12
- **Deciders:** Tech Lead
- **Supersedes:** none (realizes the wiring ADR 0027 deferred)
- **Superseded by:** none

## Context

The first **live** `topic → video` run (the last mile, CLAUDE.md §13;
issue [#107](https://github.com/MaanavA26/Reel_Automation/issues/107)) failed
at cross-verification: Groq's free tier gives each model a per-minute token
budget, and with `PLANNING`/`EXTRACTION`/`LONG_CONTEXT` all pointed at the
same model, extraction drained the shared bucket and all ~170 verification
calls 429'd inside one rate window. Zero verdicts → `VerificationError` → the
pipeline correctly refused to render.

The per-model budgets cited here (`llama-3.3-70b-versatile` **12K TPM**,
`llama-4-scout` **30K**, `gpt-oss-120b` **8K**, `llama-3.1-8b-instant` **6K**)
were **measured locally on 2026-06-12** by reading the `x-ratelimit-limit-tokens`
response header on this project's own free-tier key — they are a point-in-time
observation of one account tier, not vendor-documented guarantees, and **will
drift** (verify at `console.groq.com` when re-tuning).

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
verification on `llama-3.3-70b-versatile`, extraction on
`llama-3.1-8b-instant` (its own bucket), long-context synthesis/report/packet
on `meta-llama/llama-4-scout-17b-16e-instruct` (the largest measured bucket —
a large synthesis prompt must fit its bucket or it can *never* succeed) — so
one band cannot starve another. Free-tier limits are per-model (locally
measured, see Context — re-verify before re-tuning); the split multiplies
effective throughput with zero code change (CLAUDE.md §6 policy-driven
routing).

## Alternatives considered

- **Do nothing (manual re-runs).** Rejected: the closed-loop runner (ADR 0054)
  is unattended by design; a run that dies on the first rate window defeats it.
- **Switch provider / pay for higher tiers.** Orthogonal: any provider can 429
  or blip; the fabric should survive transients regardless of tier. Also the
  free-tier constraint is a deliberate project stance until limits truly bind
  (`docs/llm-model-selection.md` §7).
- **Wire the router-level fallback hop (`ResilientRouter`) too.** Deferred,
  not rejected: cross-model fallback changes *which model answered* — a
  quality/judgment concern ADR 0005 assigns to the Orchestrator; wiring it
  silently here would blur that boundary. Retry-same-model is judgment-free.
- **Retry inside each agent (per-call loops).** Rejected: duplicates mechanics
  across nine agents and mixes orchestration into judgment code (CLAUDE.md §4);
  the provider decorator gives one tested implementation behind the protocol.
- **Type-only narrowing (no `retry_if`).** Rejected: `httpx.HTTPStatusError`
  carries both 429 and 401; retrying auth failures would delay the loud
  config-error the composition root promises (ADR 0032).
- **Honor `Retry-After` / vendor headers.** Out of scope for now: the provider
  contract is provider-neutral and the header is not universally present;
  exponential backoff capped at the rate-window length approximates it. A
  header-aware sleeper is a compatible later refinement.

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
