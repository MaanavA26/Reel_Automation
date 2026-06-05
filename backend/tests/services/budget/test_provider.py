"""Tests for the budget-enforcing `ModelProvider` decorator.

Fully hermetic: every test wraps a `FakeProvider` and asserts both the budget
decision *and* that a blocked call never reaches the wrapped provider (so it
incurs no real spend) while an allowed call passes through.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.services.budget.provider import BudgetedModelProvider
from app.services.budget.tracker import (
    BudgetExceededError,
    BudgetLimits,
    BudgetTracker,
    PerCallEstimator,
    PriceTable,
)
from app.services.llm.fakes import FakeProvider


class _Out(BaseModel):
    x: int


def _call(provider: BudgetedModelProvider, schema: type[BaseModel]) -> BaseModel:
    return asyncio.run(
        provider.complete_structured(model="m", system="s", prompt="p", schema=schema)
    )


def _budgeted(fake: FakeProvider, *, per_run: float) -> BudgetedModelProvider:
    estimator = PerCallEstimator(PriceTable(prices={"m": 0.4}))
    tracker = BudgetTracker(limits=BudgetLimits(per_run=per_run))
    return BudgetedModelProvider(fake, estimator=estimator, tracker=tracker)


def test_calls_under_budget_pass_through() -> None:
    fake = FakeProvider([_Out(x=1), _Out(x=2)])
    budgeted = _budgeted(fake, per_run=1.0)  # 0.4/call -> 2 calls = 0.8 <= 1.0

    assert _call(budgeted, _Out) == _Out(x=1)
    assert _call(budgeted, _Out) == _Out(x=2)
    assert len(fake.calls) == 2
    assert budgeted.tracker.snapshot().run_cost == pytest.approx(0.8)


def test_call_that_would_exceed_budget_is_blocked_before_provider() -> None:
    # Ceiling 1.0, 0.4/call: 1st (0.4) + 2nd (0.8) pass; 3rd projects 1.2 > 1.0.
    fake = FakeProvider([_Out(x=1), _Out(x=2)])  # only TWO scripted responses
    budgeted = _budgeted(fake, per_run=1.0)

    assert _call(budgeted, _Out) == _Out(x=1)
    assert _call(budgeted, _Out) == _Out(x=2)

    with pytest.raises(BudgetExceededError) as exc:
        _call(budgeted, _Out)
    assert exc.value.scope == "run"

    # The blocked call must NOT have reached the fake (which has no 3rd response,
    # so a pass-through would raise "FakeProvider exhausted", not BudgetExceeded).
    assert len(fake.calls) == 2
    # Budget unchanged by the blocked attempt.
    assert budgeted.tracker.snapshot().run_cost == pytest.approx(0.8)


def test_first_call_over_budget_blocks_immediately() -> None:
    fake = FakeProvider([])  # no responses at all
    budgeted = _budgeted(fake, per_run=0.1)  # 0.4 > 0.1 on the first call

    with pytest.raises(BudgetExceededError):
        _call(budgeted, _Out)
    assert len(fake.calls) == 0  # provider never touched


def test_name_wraps_inner_provider() -> None:
    budgeted = _budgeted(FakeProvider([]), per_run=1.0)
    assert budgeted.name == "budgeted(fake)"
