# ADR 0029: LLM-as-Judge Evaluation Harness

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

CLAUDE.md §6 makes the model fabric explicitly policy-driven: roles map to
concrete models by configuration, and "LLM as a judge to find the best LLM per
task" is a stated goal. `docs/llm-model-selection.md` already did this *once*,
by hand — §3 ran the real Research Planner across candidate models, scored
schema-pass / latency / quality, and judged quality with an *independent* model
(the §3 note records a self-judging model inflating its own score to 5.0). §6
of that doc calls for productizing the eval "so it's reproducible, not throwaway."

So the question: **what is the reusable, offline-testable scaffold that answers
"which model is best for role X" — and is it an agent or a service?** Per
CLAUDE.md §4, the *judging* of quality is reasoning (an agent concern), but the
*harness* — enumerate candidates, run them, validate output, time it, tabulate,
rank — is deterministic procedure (a service). The two must not be conflated.

## Decision

**We add a new `backend/app/eval/` package: a deterministic eval-harness service
with a pluggable quality `Judge`.** It has four parts:

1. **`EvalTask`** (`task.py`) — one `(name, system, prompt, schema)` job, generic
   over its Pydantic output `schema`, exactly the shape `complete_structured`
   consumes. The harness is **schema-agnostic**: it scores *any* structured task,
   not the Planner specifically. (`ProviderRegistry` is the same
   `Mapping[str, ModelProvider]` the router uses.)
2. **`Judge` / `RuleBasedJudge` / `ModelJudge` / `QualityScore`** (`judge.py`) — a
   pluggable scorer `Protocol`. `RuleBasedJudge` (the default) scores via a
   caller-supplied pure function over the parsed output — **fully hermetic, no
   model**. `ModelJudge` is the optional LLM-as-judge: it wraps its own
   `(provider, model)` and **guards independence** (`assert_independent_of` raises
   `JudgeError` if asked to judge its own output) — the §3 self-judge caveat made
   structural. The judge's wire schema *is* `QualityScore` (no minted fields, so
   no provenance boundary to protect — unlike the Planner's `_PlannerOutput` split).
3. **`EvalResult` / `EvalReport` / `TaskRun`** (`report.py`) — typed `extra='forbid'`
   DTOs. `TaskRun` = one candidate on one task (schema-pass, latency, quality,
   error). `EvalResult` = a candidate's aggregate (pass-*rate*, median latency,
   mean quality). `EvalReport.ranking()` / `best()` / `best_choice()` apply a
   **total, lexicographic** key: schema-pass-rate first, then quality, then
   `-latency` — deterministic and reproducible (§6). `best_choice()` returns a
   `ModelChoice`, ready to drop into a `RolePolicy`.
4. **`EvalHarness`** (`harness.py`) — `run(tasks, candidates) -> EvalReport`. It
   enumerates **explicit** `(provider, model)` candidates (it *sets* policy, so it
   bypasses `for_role`), runs them **sequentially**, times each call with an
   **injected clock** (`time_fn`, defaulting to `perf_counter`), and records — never
   propagates — provider/schema failures as data points.

### Scope boundary — harness + scoring + types only

This PR ships the **scaffold**, hermetically tested with `FakeProvider`s
(scripted candidate outputs + a scripted judge) and a local failing provider. It
does **not** touch `config.py`, `main.py`, `pyproject.toml`, `router.py`, any
agent, or any workflow, and adds no live provider wiring. A live
`@pytest.mark.integration` variant and a CLI runner are deliberately deferred —
the offline scaffold is the deliverable; wiring it to real keys is a follow-up.

## Consequences

### Positive

- The §6 "make it reproducible" ask is now code: re-running an eval is a function
  call, not a hand-built script, and `best_choice()` hands a `ModelChoice`
  straight to a policy.
- Agent/tool separation (§4) held: the deterministic loop is a service; *judgment*
  is isolated behind the `Judge` protocol and can be a pure function or a model.
- The §3 self-judge lesson is structural, not advisory: `ModelJudge` cannot score
  its own output, and the guard compares **provider identity** (`.name`), not the
  registry key, so it fires even when the candidate is registered under an alias.
- Fully hermetic by default (rule-based judge + injected clock), so latency and
  ranking are deterministically testable with an instant `FakeProvider`.

### Negative

- The harness is infrastructure, not a live result: with no integration runner in
  this PR, it produces a real ranking only once wired to live providers (a
  deliberate, scoped follow-up — same staging pattern as ADR 0003's deferred
  adapter).
- Sequential execution means a large candidate-by-task grid is slow against real
  network providers; concurrency is deferred (it would muddy per-call latency
  attribution and isn't needed for correctness).

### Neutral

- The default ranking key (pass-rate, then quality, then latency) encodes §1's
  "adherence first" stance. It is one policy; a caller wanting a different
  weighting can rank off the typed `EvalResult` fields directly.

## Alternatives considered

### Option A — A throwaway `scripts/eval_models.py`

What `docs/llm-model-selection.md` §6 literally suggested. **Pros:** fastest path
to a number. **Cons:** not reusable, not unit-tested, mixes the deterministic loop
with judgment in one script; re-running drifts. **Why rejected:** the project's
component-first bar (CLAUDE.md §2/§7) wants a typed, tested, reusable service.

### Option B — A separate wire DTO for the judge (mirror `_PlannerOutput`)

Give `ModelJudge` a private `_JudgeVerdict` distinct from the public
`QualityScore`. **Pros:** symmetric with the Planner. **Cons:** the Planner split
exists to keep *minted* fields (ids/timestamps) out of model authorship;
`QualityScore` has none — `score` + `rationale` are exactly what the judge
authors. **Why rejected:** a second DTO would be ceremony with no boundary to
protect.

### Option C — Score quality at runtime on every call (online judge)

**Pros:** no separate eval step. **Cons:** multiplies cost/latency on the hot
path (`docs/llm-model-selection.md` §1.5). **Why rejected:** the eval is offline,
to *set* policy — not a per-call runtime judge.

## References

- [`docs/llm-model-selection.md`](../llm-model-selection.md) — the by-hand eval
  this productizes (§3 method, §1 selection principles, §6 "make it reproducible").
- [ADR 0003](0003-model-router-llm-fabric.md) — the model fabric (`ModelProvider`,
  `ModelRole`, `ModelRouter`, `FakeProvider`) this harness draws candidates from.
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (model routing + "LLM as a
  judge"), §7 (no speculative overbuild).
