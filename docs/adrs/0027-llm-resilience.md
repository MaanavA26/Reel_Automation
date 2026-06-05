# ADR 0027: LLM Resilience — Retry Decorator + Policy-Driven Fallback

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The model fabric (ADR 0003) selects a model by role and calls it; the concrete
adapters (ADR 0007 OpenAI-compatible, ADR 0020 Gemini) make real network calls.
Network calls are flaky — transient 429/5xx/timeout errors are routine. Today a
single transient failure propagates straight out of a node and, via ADR 0005's
`_with_failure_handling` wrapper, terminates the whole research run as `FAILED`.
That is correct as a *terminal* contract but far too brittle as the *only*
contract: a one-off rate-limit should be retried, and a persistent outage on the
primary model should fall back to the `FALLBACK`-role model CLAUDE.md §6
reserves — not abandon the run.

ADR 0005 explicitly **deferred** retries ("no flaky/transient failure source
under the offline `FakeProvider`") and named the hook as
`add_node(..., retry_policy=RetryPolicy(...))`, to land "when M-LP brings real
network faults." M-LP.1/M-LP.3 have since landed real adapters, so the trigger
condition is met. The `FALLBACK` role has been a defined-but-unused policy slot
since ADR 0003, whose §Negative noted the *trigger* logic was owned by the
Orchestrator (M4) — but M4 shipped only the deterministic failure path and
distributed the rest to consumers.

Two questions: **where does retry/fallback machinery live (agent or service?),**
and **how is it provider-neutral and hermetically testable** with no network?

## Decision

**We add `app/services/llm/resilience.py`: a `ResilientModelProvider` decorator
(bounded retry) plus a `complete_with_fallback` helper / `ResilientRouter`
wrapper (one policy-driven fallback hop).** This is the deterministic *service*
half of fault tolerance (CLAUDE.md §4); the *judgment* half — when to give up,
abandon, or escalate a run — stays with the Orchestrator (ADR 0005). Five points:

1. **`ResilientModelProvider` is a provider-level decorator.** It implements the
   `ModelProvider` protocol by wrapping an inner provider and retrying
   `complete_structured` on transient errors with bounded backoff. Because it
   satisfies the protocol it is a drop-in — the `ModelRouter` registers it under
   the inner provider's name (`name` delegates) and never knows. Retry narrows to
   the single API call.

2. **`complete_with_fallback` is a router-level helper.** Because `router.py` is
   out of scope (and stays a pure selection service), the helper *composes* a
   `ModelRouter`: call `for_role(primary)`; on terminal failure resolve
   `for_role(FALLBACK)` and try it **once**. Retry-within-a-provider, one
   fallback-hop-across-roles — no retry-of-retry. `ResilientRouter` is a thin
   convenience binding a router to this helper.

3. **Provider-neutrality by injection, never imports.** `resilience.py` imports
   no provider SDK and no `httpx` (stdlib + the fabric's own contracts only). The
   retryable exception set (`RetryConfig.retry_on`) and the async sleeper are
   constructor parameters. The transient-vs-permanent *narrowing* — the
   classification ADR 0005 §Negative said "arrives with retries + real
   providers" — happens at the future wiring site, which passes its provider's
   transient types (e.g. `httpx.TransportError` + 429/5xx). The default
   `retry_on=(Exception,)` is deliberately broad and only safe **because** the
   wiring site narrows it; an unnarrowed default would retry permanent errors.

4. **Deterministic, hermetic test seam.** A no-op async sleeper records requested
   delays so the backoff schedule is asserted with no real time passing; a local
   `_FlakyProvider` (in the test file, not `fakes.py`) raises N times then
   succeeds, so retry count and fallback engagement are asserted exactly.

5. **Capability only, no wiring** (the M-LP pattern). Nothing registers the
   decorator or calls the helper; `factory.py` / `deep_research.py` /
   `ResearchDeps` are untouched. Wiring (and the `retry_on` narrowing) lands with
   the consumer that turns it on.

### Reconciling with ADR 0005's node-level `RetryPolicy`

ADR 0005 named the retry hook as LangGraph's node-level
`RetryPolicy` — re-running the *whole node*. This ADR adds *provider-level* retry
— narrowing to the *API call*, composing **under** the node. They are
complementary, not competing: provider-level retry handles the common case (a
flaky single call) without re-running planning/parsing; node-level `RetryPolicy`
remains available for whole-step replay. Neither is the Orchestrator's
"abandon/escalate" judgment, which ADR 0005 keeps in the control band.

## Consequences

### Positive

- A transient provider error no longer ends a run; a persistent primary outage
  degrades to the policy-defined `FALLBACK` model rather than failing — the
  controlled robustness CLAUDE.md §6 intends, with no random multi-model chatter.
- The decorator is a drop-in (`ModelProvider` in, `ModelProvider` out): adopting
  retry is a registration change, not an agent or router change.
- Fully hermetic + deterministic (injected sleeper, scripted flaky fake); no
  network, no real delays, no flaky test timing.

### Negative

- The broad default `retry_on=(Exception,)` would retry permanent errors if a
  wiring site forgot to narrow it. Mitigated by documenting the contract in the
  module + this ADR; the narrowing is the wiring site's explicit job.
- Retry blindly re-issues the call — no idempotency/cost ceiling beyond
  `max_attempts`. Token-budget accounting stays deferred (ADR 0005) until a
  metering consumer exists; `max_attempts` is the only bound today.

### Neutral

- Jitter is omitted (it fights determinism); it can be added later as an injected
  RNG without changing the interface.
- One fallback hop only. A fallback *chain* (FALLBACK → FALLBACK-2) is not
  modelled — no consumer needs more than one tier yet.

## Alternatives considered

### Option A — Rely solely on LangGraph node-level `RetryPolicy`

Use `add_node(..., retry_policy=...)` and skip a provider decorator. **Pros:**
built in; no new module. **Cons:** re-runs the entire node (re-planning,
re-parsing) for a flaky single call; and `RetryPolicy` does retry, not *fallback*
to a different model — the `FALLBACK` role would stay unused. **Why rejected:**
provider-level retry is the narrower, cheaper fix and the only one that engages
the policy's fallback slot; node-level retry remains complementary (see above).

### Option B — Build retry/fallback into the Orchestrator as agent logic

Put it in the control-band agent. **Pros:** matches the §5.6 "Orchestrator
Agent." **Cons:** mechanical bounded retry + a dictionary-lookup fallback hop are
deterministic procedure, not judgment — modelling them as agent reasoning
violates CLAUDE.md §4. **Why rejected:** the service/judgment split is the point;
the Orchestrator keeps the "when to abandon" decision, not the retry mechanics.

## References

- Related: [ADR 0003](0003-model-router-llm-fabric.md) (the fabric + the
  `FALLBACK` policy slot this engages), [ADR 0005](0005-workflow-error-handling.md)
  (deferred retries/budgets + the node-level `RetryPolicy` hook reconciled here),
  [ADR 0007](0007-openai-compatible-llm-adapter.md) /
  [ADR 0020](0020-gemini-native-adapter.md) (the real adapters whose transient
  errors this guards).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP (real providers / resilience).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (FALLBACK role,
  policy-driven routing), §7 (no speculative overbuild).
