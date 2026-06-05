"""The eval harness — run a task suite across candidate models, score, rank.

`EvalHarness` is a deterministic *service* (CLAUDE.md §4): given a task suite and
a set of explicit `(provider, model)` candidates, it runs each candidate on each
task, records schema-pass + latency, asks the pluggable `Judge` for a quality
score, and returns a typed `EvalReport` whose `best()` answers "which model is
best for role X" (CLAUDE.md §6). It mirrors the methodology of
``docs/llm-model-selection.md`` §3 (a candidate-by-task grid, independent judge),
productized as the reusable, offline-testable scaffold that doc's §6 calls for.

Design notes:
- **Candidates are enumerated explicitly, not routed by role.** The eval *sets*
  policy; it does not consume one — so it bypasses `ModelRouter.for_role` and
  calls providers directly via `ModelChoice(provider, model)`.
- **Latency uses an injected clock** (``time_fn``) so hermetic tests with an
  instant `FakeProvider` can script deterministic timings.
- **A schema failure is a data point, not a crash.** A candidate that fails to
  produce a valid instance, or whose judge errors, is recorded on its `TaskRun`
  and the run continues.
- **Sequential** by design: per-call latency attribution stays clean. Concurrency
  across candidates/tasks is a deferred optimization, not needed for correctness.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from app.eval.judge import Judge, RuleBasedJudge
from app.eval.report import EvalReport, EvalResult, TaskRun
from app.eval.task import EvalTask, ProviderRegistry
from app.services.llm.router import ModelChoice


class EvalConfigError(RuntimeError):
    """Raised on a malformed eval configuration (e.g. an unregistered provider)."""


class EvalHarness:
    """Runs candidate models against a task suite and scores them.

    Holds the provider registry (named adapters, the shape `ModelRouter` uses) and
    a `Judge`. ``time_fn`` defaults to `time.perf_counter`; tests inject a scripted
    clock for deterministic latency assertions.
    """

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        judge: Judge | None = None,
        time_fn: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._providers = dict(providers)
        self._judge: Judge = judge if judge is not None else RuleBasedJudge()
        self._time_fn = time_fn

    async def run(
        self,
        *,
        tasks: Sequence[EvalTask[BaseModel]],
        candidates: Sequence[ModelChoice],
    ) -> EvalReport:
        """Run every candidate against every task and return a scored `EvalReport`.

        Raises `EvalConfigError` if ``candidates`` is empty or names a provider not
        in the registry, or `JudgeError` if a `ModelJudge` would judge one of the
        candidates' own output — all surfaced *before any call*, so a misconfigured
        run fails loud rather than producing a misleading partial ranking. Each
        candidate's `EvalResult` carries one `TaskRun` per task, in suite order.
        """
        if not candidates:
            raise EvalConfigError("no candidates to evaluate")
        self._validate_candidates(candidates)
        self._assert_judge_independent(candidates)

        results: list[EvalResult] = []
        for choice in candidates:
            runs = [await self._run_one(choice=choice, task=task) for task in tasks]
            results.append(EvalResult(provider=choice.provider, model=choice.model, runs=runs))
        return EvalReport(results=results)

    def _validate_candidates(self, candidates: Sequence[ModelChoice]) -> None:
        for choice in candidates:
            if choice.provider not in self._providers:
                raise EvalConfigError(f"candidate names unregistered provider {choice.provider!r}")

    def _assert_judge_independent(self, candidates: Sequence[ModelChoice]) -> None:
        """Reject up-front any candidate a `ModelJudge` would judge as its own output.

        Independence compares the judge against each candidate's *provider name*
        (the adapter's ``.name``), not the registry key — the registry key is a
        local alias and may differ from the provider's identity, so comparing keys
        would let a genuine self-judge slip through when key != name. Run before the
        eval loop so a self-judge misconfiguration fails loud, not after partially
        executing earlier candidates (symmetric with `_validate_candidates`).
        """
        independence = getattr(self._judge, "assert_independent_of", None)
        if not callable(independence):
            return
        for choice in candidates:
            independence(provider=self._providers[choice.provider].name, model=choice.model)

    async def _run_one(
        self,
        *,
        choice: ModelChoice,
        task: EvalTask[BaseModel],
    ) -> TaskRun:
        """Run one candidate on one task, capturing schema-pass, latency, quality.

        Provider errors (including schema-coercion failures) are caught and
        recorded — never propagated — so one bad candidate cannot abort the suite.
        """
        provider = self._providers[choice.provider]
        start = self._time_fn()
        try:
            output: BaseModel = await provider.complete_structured(
                model=choice.model,
                system=task.system,
                prompt=task.prompt,
                schema=task.schema,
            )
        except Exception as exc:  # any provider failure is a data point, not a crash
            return TaskRun(
                task_name=task.name,
                schema_passed=False,
                latency_s=self._time_fn() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        latency_s = self._time_fn() - start

        quality, rationale, judge_error = await self._judge_output(task=task, output=output)
        return TaskRun(
            task_name=task.name,
            schema_passed=True,
            latency_s=latency_s,
            quality=quality,
            quality_rationale=rationale,
            error=judge_error,
        )

    async def _judge_output(
        self,
        *,
        task: EvalTask[BaseModel],
        output: BaseModel,
    ) -> tuple[float | None, str | None, str | None]:
        """Score one output; return ``(quality, rationale, error)``.

        Judge *independence* is enforced once, up-front (`_assert_judge_independent`),
        so by here the judge is known-independent. A judge *runtime* failure here is
        recorded as a per-run error (quality unscored), not propagated — a flaky
        judge call shouldn't lose an otherwise-valid candidate run.
        """
        try:
            score = await self._judge.score(task=task, output=output)
        except Exception as exc:  # a judge failure shouldn't lose the candidate's run
            return None, None, f"judge error: {type(exc).__name__}: {exc}"
        return score.score, score.rationale, None
