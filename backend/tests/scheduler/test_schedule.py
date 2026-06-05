"""Pure next-run math for `IntervalSchedule` / `DailySchedule` (ADR 0034).

Fully hermetic and clock-free: every case passes an explicit ``reference`` instant
and asserts the returned slot — no real time passes, nothing sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from app.scheduler.schedule import DailySchedule, IntervalSchedule


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# --- IntervalSchedule --------------------------------------------------------


def test_interval_anchored_slot_strictly_after_reference() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    # 13:00 sits between the 12:00 and 18:00 slots -> next is 18:00.
    assert sched.next_run_after(_utc(2026, 6, 1, 13)) == _utc(2026, 6, 1, 18)


def test_interval_on_slot_advances_to_next() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    # Strictly-after: exactly on the 12:00 slot must return 18:00, not 12:00.
    assert sched.next_run_after(_utc(2026, 6, 1, 12)) == _utc(2026, 6, 1, 18)


def test_interval_anchoring_is_reproducible_regardless_of_poll_time() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    # Two different poll instants inside the same gap resolve to the same slot.
    slot_a = sched.next_run_after(_utc(2026, 6, 1, 6, 1))
    slot_b = sched.next_run_after(_utc(2026, 6, 1, 11, 59))
    assert slot_a == slot_b == _utc(2026, 6, 1, 12)


def test_interval_before_anchor_returns_anchor() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    assert sched.next_run_after(_utc(2026, 5, 31, 23)) == _utc(2026, 6, 1, 0)


def test_interval_on_slot_advances_for_non_float_exact_interval() -> None:
    # A 7-minute interval is not float-exact under timedelta-ratio division;
    # integer floor-division keeps strictly-after honest here. Slot 21 is
    # 00:00 + 21*7min = 02:27 exactly — must advance to slot 22 (02:34).
    sched = IntervalSchedule(interval=timedelta(minutes=7), anchor=_utc(2026, 6, 1, 0))
    on_slot = _utc(2026, 6, 1, 0) + 21 * timedelta(minutes=7)
    assert sched.next_run_after(on_slot) == on_slot + timedelta(minutes=7)


def test_interval_normalizes_non_utc_reference() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    # 13:00 in +02:00 == 11:00 UTC, which falls in the 06:00-12:00 gap -> 12:00 UTC.
    ref = datetime(2026, 6, 1, 13, tzinfo=timezone(timedelta(hours=2)))
    assert sched.next_run_after(ref) == _utc(2026, 6, 1, 12)


def test_interval_rejects_naive_reference() -> None:
    sched = IntervalSchedule(interval=timedelta(hours=6), anchor=_utc(2026, 6, 1, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        sched.next_run_after(datetime(2026, 6, 1, 13))


def test_interval_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        IntervalSchedule(interval=timedelta(0), anchor=_utc(2026, 6, 1, 0))


def test_interval_rejects_naive_anchor() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        IntervalSchedule(interval=timedelta(hours=1), anchor=datetime(2026, 6, 1))


# --- DailySchedule -----------------------------------------------------------


def test_daily_picks_next_time_today() -> None:
    sched = DailySchedule(times=(time(9, 0), time(18, 0)))
    assert sched.next_run_after(_utc(2026, 6, 1, 10)) == _utc(2026, 6, 1, 18)


def test_daily_rolls_to_tomorrow_after_last_time() -> None:
    sched = DailySchedule(times=(time(9, 0), time(18, 0)))
    assert sched.next_run_after(_utc(2026, 6, 1, 20)) == _utc(2026, 6, 2, 9)


def test_daily_on_a_slot_advances_to_next() -> None:
    sched = DailySchedule(times=(time(9, 0), time(18, 0)))
    # Strictly-after: exactly 09:00 -> 18:00 same day.
    assert sched.next_run_after(_utc(2026, 6, 1, 9)) == _utc(2026, 6, 1, 18)


def test_daily_on_last_slot_rolls_to_tomorrow() -> None:
    sched = DailySchedule(times=(time(9, 0), time(18, 0)))
    assert sched.next_run_after(_utc(2026, 6, 1, 18)) == _utc(2026, 6, 2, 9)


def test_daily_normalizes_and_dedupes_times() -> None:
    sched = DailySchedule(times=(time(18, 0), time(9, 0), time(9, 0)))
    assert sched.times == (time(9, 0), time(18, 0))


def test_daily_rejects_empty_times() -> None:
    with pytest.raises(ValueError, match="at least one time"):
        DailySchedule(times=())


def test_daily_rejects_aware_times() -> None:
    with pytest.raises(ValueError, match="naive"):
        DailySchedule(times=(time(9, 0, tzinfo=UTC),))


def test_daily_rejects_naive_reference() -> None:
    sched = DailySchedule(times=(time(9, 0),))
    with pytest.raises(ValueError, match="timezone-aware"):
        sched.next_run_after(datetime(2026, 6, 1, 10))
