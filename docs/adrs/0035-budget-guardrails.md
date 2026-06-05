# ADR 0035: Budget Guardrails (Cost-Enforcing Model-Fabric Decorator)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Unattended Deep Research runs (the autonomous "non-stop" build cadence) issue
many LLM calls with no human watching the meter. A planning loop that fails to
converge, a runaway fan-out, or a misconfigured policy can rack up real API
spend before anyone notices. The pipeline needs a hard *guardrail* — not just an
observability dashboard — that **refuses the call which would push spend past a
ceiling**.

ADR 0005 explicitly deferred "retries/budgets": run-scoped budget *limits* were
to flow via `config`, with "the consumption tally added with its consumer." This
ADR is that consumer. The model fabric (ADR 0003) gives the seam: every provider
implements one `ModelProvider.complete_structured(*, model, system, prompt,
schema)` contract, so a budget gate can live entirely at that boundary and
compose around *any* provider — exactly as the response cache (ADR 0026) and
resilience layer (ADR 0027) already do.

Per CLAUDE.md §4 this is **service/tool** work — deterministic, judgment-free
metering + enforcement — not an agent. The *judgment* half (how a run reacts to
being blocked: abandon, downgrade the role, escalate) stays with the
Orchestrator, consistent with ADR 0005.

The open questions were narrow: (1) when to record usage relative to the call,
(2) how to estimate cost when the provider contract returns no token-usage
metadata, (3) how to keep both the price table and the estimator pluggable
without over-building, and (4) how to be honest about estimate-vs-actual drift.

## Decision

**We add a new `app/services/budget/` package with two composable pieces,
mirroring the fabric's decorator pattern.**

1. **`BudgetTracker` + pluggable `CostEstimator` (the accounting/enforcement
   core, `tracker.py`).** The tracker accrues call counts and estimated dollar
   cost scoped per-*run* (its whole lifetime) and per-*calendar-day* (via an
   injected `Clock`). `charge(cost)` projects `accrued + cost` against each
   configured ceiling and raises `BudgetExceededError` *before* the cost is
   spent; on success it accrues immediately. Cost comes from a `CostEstimator`
   protocol over a configurable `PriceTable`; two stdlib estimators ship —
   `PerCallEstimator` (flat $/call) and `TokenCostEstimator` ($/1k tokens via a
   pluggable `TokenCounter`, default the dependency-free `HeuristicTokenCounter`,
   ~4 chars/token).

2. **`BudgetedModelProvider` (the enforcing decorator, `provider.py`).** Wraps
   any `ModelProvider`; on `complete_structured` it estimates the call, calls
   `tracker.charge` (which raises *before* the wrapped call if a ceiling would
   break), and only then calls through. A blocked call never reaches the inner
   provider, so it incurs **no real API spend**. `name` wraps as
   `budgeted(...)` so the guard is visible in diagnostics.

### Key design decisions (the parts a test alone won't catch)

- **Pre-call reservation, not post-call recording.** Cost is accrued at
  check-time, before the call. This closes the concurrency window where two
  in-flight calls both pass a check before either records (which would overshoot
  the ceiling), and it conservatively counts the *attempt* even if the call later
  fails. Both are the safe direction for a spend guard: it errs toward blocking
  sooner, never toward silent overshoot.
- **Unmodeled model fails loud.** A model absent from the `PriceTable` raises
  `UnknownModelError` (mirroring the router's `UnknownProviderError` posture),
  never a silent $0 — a $0 estimate would let an unmodeled model bypass the
  budget entirely. A caller may set `default_price` *deliberately* to opt into a
  catch-all.
- **Both ceilings optional and independent.** `per_run` and `per_day` are each
  `None`-able; the tracker checks both and blocks if *either* would break.
  `BudgetExceededError` carries `scope` / `limit` / `accrued` / `projected` so a
  consumer routes on data, not a parsed string.
- **Calendar-day rollover.** Day scope resets on the injected clock's UTC date
  changing; the per-run tally never resets. Asserted hermetically by advancing a
  stub clock past midnight.
- **Boundary is strict `>`.** A projection landing *exactly* on the ceiling is
  allowed; only a projection *over* it blocks. Tested under, at, and over.

### Scope boundary

This PR ships only the `budget/` package + hermetic tests + this ADR. It does
**not** wire the decorator into the router/`default_policy`, read limits from
`config`, or wrap any provider — that is a deliberate composition-root change for
a follow-up, owned by the Orchestrator consumer (it decides how to react to a
block). Stdlib only; no new dependency; no edits to `config.py`, `main.py`,
`router.py`, or `base.py`.

## Consequences

### Positive

- An unattended run cannot silently overspend: once a ceiling is hit, the next
  call raises *before* any provider round-trip.
- Provider-neutral: composes around the ADR 0003 contract, works for every
  provider (OpenAI-compatible, Gemini, fakes) with zero caller changes, and
  stacks with the cache/resilience decorators.
- Fully hermetic and deterministic: tests wrap a `FakeProvider` and a stub clock
  and assert the blocked call never reaches the provider (the fake, seeded with
  too few responses, would raise *exhausted* on a wrongful pass-through).

### Negative

- **Estimate-vs-actual drift (the core caveat).** The `ModelProvider` contract
  returns no token-usage metadata, so the guard enforces against a *pre-call,
  input-only estimate*, not the actual billed cost. The token estimator's count
  is heuristic and ignores output tokens; per-call pricing ignores size entirely.
  The tally is a *conservative ceiling*, not an invoice. Documented in the module
  docstring; tune the price table / token counter to the provider's real billing.
- **Process-local state.** The tracker lives in memory for one run; there is no
  shared/persistent cross-process budget. A persistent backend is a future
  plug-in, not built now (avoids speculative surface, CLAUDE.md §7).

### Neutral

- **Attempts count against budget even on failure.** A reserved-then-failed call
  still consumes budget. This is the deliberate safe direction (above); named
  here so a reviewer reads it as a decision, not an oversight.

## Alternatives considered

### Option A — Observe only (metering dashboard, no enforcement)

Record usage and surface it, but never block. **Cons:** does not stop runaway
spend on an unattended run — the exact failure mode this targets. **Rejected:**
the task is a *guardrail*, so enforcement is the point; metering is the
by-product.

### Option B — Record cost *after* the call (count only successes)

Charge post-call so a failed call costs nothing. **Cons:** opens a concurrency
overshoot window and lets a burst of in-flight calls all pass a stale check.
**Rejected:** pre-call reservation is conservative and concurrency-safe; the
"count the attempt" trade is acceptable and documented.

### Option C — Silent $0 for unmodeled models

Default a missing model's price to $0. **Cons:** the single biggest guardrail
hole — an unpriced model bypasses the budget entirely. **Rejected:** unmodeled
fails loud; a caller opts into a catch-all via explicit `default_price`.

### Option D — Bake the budget into the router / `default_policy`

Wrap every resolved provider automatically. **Cons:** makes enforcement (and its
estimate-vs-actual trade) a silent default, and the Orchestrator — not the
router — owns how to react to a block. **Rejected:** composed explicitly by the
consumer, like the cache (ADR 0026).

## References

- Related: [ADR 0003 — Model Router and LLM Fabric](0003-model-router-llm-fabric.md)
  (the `ModelProvider` contract this decorator wraps).
- Related: [ADR 0005 — Workflow Error Handling](0005-workflow-error-handling.md)
  (deferred "retries/budgets" to a consumer — this ADR).
- Related: [ADR 0026 — LLM Response Cache](0026-llm-response-cache.md) and
  [ADR 0027 — LLM Resilience](0027-llm-resilience.md) (the decorator pattern this
  mirrors and stacks with).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (model fabric), §7 (no
  speculative overbuild), §9 (scope discipline).
