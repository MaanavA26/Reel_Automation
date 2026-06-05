"""Hermetic tests for the LLM-as-judge eval harness (`app.eval`).

Fully offline: the candidate models are `FakeProvider`s replaying scripted,
schema-valid outputs; quality is scored either by a deterministic `RuleBasedJudge`
or by a `ModelJudge` backed by *another* `FakeProvider` replaying a scripted
verdict (the "scripted judge"). A tiny local `_FailingProvider` exercises the
schema-fail-as-data-point path without touching the shared `fakes.py`.

These tests assert the harness contract: candidate-by-task grid, schema-pass /
latency / quality recording, deterministic ranking with the documented tie-break,
the judge-independence guard, and graceful per-run failure handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest
from pydantic import BaseModel

from app.eval import (
    EvalConfigError,
    EvalHarness,
    EvalReport,
    EvalResult,
    EvalTask,
    JudgeError,
    ModelJudge,
    QualityScore,
    RuleBasedJudge,
    TaskRun,
)
from app.services.llm.base import StructuredT
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice

# --- fixtures: a tiny task schema + helpers ---------------------------------


class _Answer(BaseModel):
    """A trivial structured-output schema for the eval tasks under test."""

    text: str
    length: int


def _task(name: str = "t1") -> EvalTask[_Answer]:
    return EvalTask(name=name, system="answer well", prompt="the question", schema=_Answer)


def _scripted_clock(ticks: list[float]) -> Callable[[], float]:
    """A deterministic clock: returns ``ticks`` in order on each call.

    The harness calls the clock twice per run (start, end); supply pairs.
    """
    it: Iterator[float] = iter(ticks)
    return lambda: next(it)


class _FailingProvider:
    """A `ModelProvider` whose call always raises — the schema-fail data point.

    Local to the test module (not added to the shared `fakes.py`, which is out of
    the eval package's scope): a real provider failing to coerce schema is exactly
    this — an exception the harness must catch and record, not propagate.
    """

    name = "broken"

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        raise ValueError("could not produce valid JSON")


# --- core grid + recording ---------------------------------------------------


def test_runs_every_candidate_on_every_task() -> None:
    fast = FakeProvider([_Answer(text="a", length=1), _Answer(text="b", length=1)])
    slow = FakeProvider([_Answer(text="c", length=1), _Answer(text="d", length=1)])
    harness = EvalHarness(providers={"fast": fast, "slow": slow})
    report = asyncio.run(
        harness.run(
            tasks=[_task("t1"), _task("t2")],
            candidates=[ModelChoice("fast", "m1"), ModelChoice("slow", "m2")],
        )
    )
    assert len(report.results) == 2
    assert all(len(r.runs) == 2 for r in report.results)
    assert [run.task_name for run in report.results[0].runs] == ["t1", "t2"]
    assert all(run.schema_passed for r in report.results for run in r.runs)


def test_latency_recorded_from_injected_clock() -> None:
    # start=10.0, end=11.5 -> 1.5s latency; deterministic, not wall-clock.
    fake = FakeProvider([_Answer(text="a", length=1)])
    harness = EvalHarness(providers={"p": fake}, time_fn=_scripted_clock([10.0, 11.5]))
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("p", "m")]))
    assert report.results[0].runs[0].latency_s == pytest.approx(1.5)


# --- schema-pass-as-data-point ------------------------------------------------


def test_schema_failure_is_recorded_not_raised() -> None:
    harness = EvalHarness(providers={"broken": _FailingProvider()})
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("broken", "m")]))
    run = report.results[0].runs[0]
    assert run.schema_passed is False
    assert run.quality is None
    assert run.error is not None and "ValueError" in run.error


def test_mixed_pass_and_fail_aggregates_to_a_rate() -> None:
    # One task passes, one fails -> 0.5 schema-pass rate. Fake raises (exhausted)
    # on the 2nd call, so the second task records a failure.
    fake = FakeProvider([_Answer(text="a", length=1)])
    harness = EvalHarness(providers={"p": fake})
    report = asyncio.run(
        harness.run(tasks=[_task("t1"), _task("t2")], candidates=[ModelChoice("p", "m")])
    )
    assert report.results[0].schema_pass_rate == pytest.approx(0.5)


# --- rule-based (hermetic default) judge -------------------------------------


def test_rule_based_judge_scores_from_output() -> None:
    def score_fn(output: BaseModel) -> QualityScore:
        assert isinstance(output, _Answer)
        return QualityScore(score=float(output.length), rationale="by length")

    fake = FakeProvider([_Answer(text="hello", length=5)])
    harness = EvalHarness(providers={"p": fake}, judge=RuleBasedJudge(score_fn))
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("p", "m")]))
    run = report.results[0].runs[0]
    assert run.quality == pytest.approx(5.0)
    assert run.quality_rationale == "by length"


def test_default_judge_leaves_quality_baseline() -> None:
    fake = FakeProvider([_Answer(text="a", length=1)])
    harness = EvalHarness(providers={"p": fake})  # default RuleBasedJudge, no score_fn
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("p", "m")]))
    assert report.results[0].runs[0].quality == pytest.approx(0.0)


# --- model judge (scripted, independent) -------------------------------------


def test_model_judge_scores_via_scripted_independent_provider() -> None:
    candidate = FakeProvider([_Answer(text="a", length=1)])
    # The judge's wire schema is `QualityScore`, so script one directly.
    judge_provider = FakeProvider([QualityScore(score=4.0, rationale="solid")])
    judge_provider.name = "judge"  # distinct from the candidate provider
    judge = ModelJudge(provider=judge_provider, model="judge-model")
    harness = EvalHarness(providers={"cand": candidate}, judge=judge)
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("cand", "m")]))
    run = report.results[0].runs[0]
    assert run.quality == pytest.approx(4.0)
    assert run.quality_rationale == "solid"
    # The judge requested its own schema (QualityScore), not the task's (_Answer).
    assert judge_provider.calls[0].schema is QualityScore


def test_self_judge_is_rejected() -> None:
    # Same provider object serves as both candidate and judge -> JudgeError.
    fake = FakeProvider([_Answer(text="a", length=1)])
    judge = ModelJudge(provider=fake, model="m")  # provider.name == "fake", model == "m"
    harness = EvalHarness(providers={"fake": fake}, judge=judge)
    with pytest.raises(JudgeError):
        asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("fake", "m")]))


def test_self_judge_rejected_even_when_registry_key_differs() -> None:
    # The candidate is registered under a key ("primary") that differs from the
    # provider's identity (.name == "shared"); the judge is the *same* object +
    # model id. Independence must compare provider identity, not the registry key,
    # or a genuine self-judge slips through.
    shared = FakeProvider([_Answer(text="a", length=1)])
    shared.name = "shared"
    judge = ModelJudge(provider=shared, model="m")
    harness = EvalHarness(providers={"primary": shared}, judge=judge)
    with pytest.raises(JudgeError):
        asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("primary", "m")]))


def test_judge_error_is_recorded_not_lost() -> None:
    candidate = FakeProvider([_Answer(text="a", length=1)])
    judge_provider = FakeProvider([])  # exhausted -> judge call raises AssertionError
    judge_provider.name = "judge"
    judge = ModelJudge(provider=judge_provider, model="judge-model")
    harness = EvalHarness(providers={"cand": candidate}, judge=judge)
    report = asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("cand", "m")]))
    run = report.results[0].runs[0]
    assert run.schema_passed is True  # the candidate succeeded
    assert run.quality is None
    assert run.error is not None and "judge error" in run.error


# --- ranking (deterministic, documented tie-break) ---------------------------


def _result(
    provider: str, *, passed: list[bool], latency: float, quality: float | None
) -> EvalResult:
    runs = [
        TaskRun(
            task_name=f"t{i}",
            schema_passed=p,
            latency_s=latency,
            quality=quality if p else None,
        )
        for i, p in enumerate(passed)
    ]
    return EvalResult(provider=provider, model="m", runs=runs)


def test_ranking_schema_pass_dominates_quality() -> None:
    flaky_genius = _result("a", passed=[True, False], latency=1.0, quality=5.0)  # 0.5 pass
    reliable = _result("b", passed=[True, True], latency=9.0, quality=3.0)  # 1.0 pass
    report = EvalReport(results=[flaky_genius, reliable])
    assert report.best().provider == "b"  # adherence is co-equal/first


def test_ranking_quality_breaks_pass_ties_then_latency() -> None:
    hi_q = _result("a", passed=[True], latency=5.0, quality=4.0)
    lo_q_fast = _result("b", passed=[True], latency=0.1, quality=2.0)
    report = EvalReport(results=[lo_q_fast, hi_q])
    assert report.best().provider == "a"  # quality beats latency

    # equal pass + equal quality -> faster wins
    slow = _result("c", passed=[True], latency=5.0, quality=4.0)
    tie = EvalReport(results=[slow, hi_q.model_copy(update={"provider": "fast"})])
    # both quality 4.0, pass 1.0; "fast" has latency 5.0 too -> stable order keeps input order
    assert [r.provider for r in tie.ranking()] == ["c", "fast"]


def test_best_choice_returns_model_choice_for_policy() -> None:
    winner = _result("groq", passed=[True], latency=1.2, quality=4.6)
    other = _result("hf", passed=[True], latency=8.9, quality=4.4)
    report = EvalReport(results=[winner, other])
    choice = report.best_choice()
    assert (choice.provider, choice.model) == ("groq", "m")


def test_empty_report_best_raises() -> None:
    with pytest.raises(ValueError):
        EvalReport(results=[]).best()


# --- config guards ------------------------------------------------------------


def test_no_candidates_raises() -> None:
    harness = EvalHarness(providers={"p": FakeProvider([])})
    with pytest.raises(EvalConfigError):
        asyncio.run(harness.run(tasks=[_task()], candidates=[]))


def test_unregistered_provider_raises_before_calling() -> None:
    fake = FakeProvider([_Answer(text="a", length=1)])
    harness = EvalHarness(providers={"p": fake})
    with pytest.raises(EvalConfigError):
        asyncio.run(harness.run(tasks=[_task()], candidates=[ModelChoice("ghost", "m")]))
    assert fake.calls == []  # failed loud before any call
