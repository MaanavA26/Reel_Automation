"""Tests for the budget accounting + enforcement core.

Fully hermetic and deterministic: a stub clock drives calendar-day rollover
without real time passing, and every estimator runs on a fixed price table.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.budget.tracker import (
    BudgetError,
    BudgetExceededError,
    BudgetLimits,
    BudgetTracker,
    HeuristicTokenCounter,
    PerCallEstimator,
    PriceTable,
    TokenCostEstimator,
    UnknownModelError,
)


def _clock_at(*times: datetime):
    """A stub clock returning each given time in order, then repeating the last."""
    seq = list(times)

    def clock() -> datetime:
        return seq[0] if len(seq) == 1 else seq.pop(0)

    return clock


# --- PriceTable -----------------------------------------------------------


def test_price_table_returns_configured_price() -> None:
    table = PriceTable(prices={"m1": 0.5})
    assert table.price_for("m1") == 0.5


def test_unmodeled_model_fails_loud() -> None:
    table = PriceTable(prices={"m1": 0.5})
    with pytest.raises(UnknownModelError):
        table.price_for("unknown")


def test_default_price_applies_to_unlisted_model_when_set() -> None:
    table = PriceTable(prices={"m1": 0.5}, default_price=0.1)
    assert table.price_for("anything") == 0.1


# --- Estimators -----------------------------------------------------------


def test_per_call_estimator_is_flat_per_model() -> None:
    est = PerCallEstimator(PriceTable(prices={"m": 0.02}))
    assert est.estimate(model="m", system="x" * 100, prompt="y" * 999) == 0.02


def test_token_cost_estimator_scales_with_input_tokens() -> None:
    # 4 chars/token heuristic: system=4 chars -> 1 tok, prompt=8 chars -> 2 tok,
    # total 3 tokens at $2/1k tokens = 3/1000 * 2 = 0.006.
    est = TokenCostEstimator(PriceTable(prices={"m": 2.0}))
    cost = est.estimate(model="m", system="abcd", prompt="abcdefgh")
    assert cost == pytest.approx(0.006)


def test_token_cost_estimator_unmodeled_model_fails_loud() -> None:
    est = TokenCostEstimator(PriceTable(prices={"m": 2.0}))
    with pytest.raises(UnknownModelError):
        est.estimate(model="other", system="a", prompt="b")


def test_heuristic_token_counter_ceil_and_empty() -> None:
    counter = HeuristicTokenCounter(chars_per_token=4)
    assert counter.count("") == 0
    assert counter.count("a") == 1  # ceil(1/4)
    assert counter.count("abcd") == 1
    assert counter.count("abcde") == 2  # ceil(5/4)


def test_heuristic_token_counter_rejects_non_positive() -> None:
    with pytest.raises(BudgetError):
        HeuristicTokenCounter(chars_per_token=0)


# --- BudgetLimits ---------------------------------------------------------


def test_negative_ceiling_rejected() -> None:
    with pytest.raises(BudgetError):
        BudgetLimits(per_run=-1.0)


# --- BudgetTracker enforcement -------------------------------------------


def test_charge_under_ceiling_accrues() -> None:
    tracker = BudgetTracker(limits=BudgetLimits(per_run=1.0))
    tracker.charge(0.3)
    tracker.charge(0.3)
    snap = tracker.snapshot()
    assert snap.run_calls == 2
    assert snap.run_cost == pytest.approx(0.6)


def test_charge_exactly_at_ceiling_is_allowed() -> None:
    # Boundary: projected == ceiling is allowed (strict > blocks).
    tracker = BudgetTracker(limits=BudgetLimits(per_run=1.0))
    tracker.charge(0.6)
    tracker.charge(0.4)  # projects to exactly 1.0 -> allowed
    assert tracker.snapshot().run_cost == pytest.approx(1.0)


def test_charge_over_run_ceiling_blocks_before_accrual() -> None:
    tracker = BudgetTracker(limits=BudgetLimits(per_run=1.0))
    tracker.charge(0.9)
    with pytest.raises(BudgetExceededError) as exc:
        tracker.charge(0.2)  # projects to 1.1 > 1.0
    assert exc.value.scope == "run"
    assert exc.value.limit == 1.0
    assert exc.value.accrued == pytest.approx(0.9)
    assert exc.value.projected == pytest.approx(1.1)
    # Blocked charge must NOT have accrued.
    assert tracker.snapshot().run_cost == pytest.approx(0.9)
    assert tracker.snapshot().run_calls == 1


def test_per_day_ceiling_blocks_independently_of_run() -> None:
    # No run ceiling; only a day ceiling. Day cap trips.
    tracker = BudgetTracker(limits=BudgetLimits(per_day=0.5))
    tracker.charge(0.5)
    with pytest.raises(BudgetExceededError) as exc:
        tracker.charge(0.01)
    assert exc.value.scope == "day"


def test_no_limits_never_blocks() -> None:
    tracker = BudgetTracker()  # both ceilings None
    for _ in range(100):
        tracker.charge(1000.0)
    assert tracker.snapshot().run_calls == 100


def test_negative_cost_rejected() -> None:
    tracker = BudgetTracker()
    with pytest.raises(BudgetError):
        tracker.charge(-0.1)


def test_day_rollover_resets_daily_but_not_run() -> None:
    day1 = datetime(2026, 6, 5, 23, 0, tzinfo=UTC)
    day2 = datetime(2026, 6, 6, 0, 30, tzinfo=UTC)
    clock = _clock_at(day1, day1, day2, day2)
    tracker = BudgetTracker(limits=BudgetLimits(per_day=1.0), clock=clock)

    tracker.charge(0.8)  # day1
    snap1 = tracker.snapshot()  # day1 (3rd clock read is day1)
    assert snap1.day_cost == pytest.approx(0.8)

    # Now clock advances past midnight: daily tally must reset, run tally persists.
    tracker.charge(0.8)  # day2 — would breach day cap if not reset
    snap2 = tracker.snapshot()  # day2
    assert snap2.day == day2.date()
    assert snap2.day_cost == pytest.approx(0.8)  # daily reset
    assert snap2.run_cost == pytest.approx(1.6)  # run persisted across days
    assert snap2.run_calls == 2
