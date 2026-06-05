"""Pure next-run computation for the unattended batch loop.

This module is the *timing brain* of the scheduler and contains **no waiting and
no clock of its own**. A `Schedule` is a small immutable config object whose only
behavior is the pure function ``next_run_after(reference) -> datetime``: given a
reference instant, it returns the next instant the schedule should fire. The
*caller* owns the clock — it passes in ``datetime.now(UTC)`` (or, in tests, a
scripted instant) — so the entire computation is deterministic and unit-testable
without any real sleeping (CLAUDE.md §7 testability; mirrors the injected-clock
convention already used by `app.eval.harness`).

Two concrete schedules cover the N-videos-per-day target:

* `IntervalSchedule` — fire every fixed `interval` (e.g. every 6 hours), anchored
  to a fixed `anchor` instant so the fire times are stable and reproducible
  regardless of *when* the loop happens to ask (``anchor + k*interval`` for the
  smallest ``k`` strictly after ``reference``).
* `DailySchedule` — fire at fixed wall-clock times of day (e.g. 09:00 and 18:00),
  **UTC-only** in this v1 (a fixed-time-of-day schedule is inherently wall-clock;
  DST/timezone handling is a documented follow-up — see the class docstring).

Both use **strictly-after** semantics: if ``reference`` lands exactly on a fire
time, the *next* slot is returned, never ``reference`` itself — so a loop that
fires and immediately recomputes does not re-fire the same slot. Both also
**reject naive datetimes** (a naive reference silently producing wrong slots is
the classic bug), consistent with the schema's tz-aware-UTC convention (ADR 0001).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta


def _require_aware(reference: datetime) -> None:
    """Raise if ``reference`` is naive (no tzinfo).

    Next-run math over a naive instant is ambiguous and a frequent source of
    off-by-one/timezone bugs, so we fail loud at the boundary rather than emit a
    silently-wrong slot.
    """
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("next_run_after requires a timezone-aware datetime")


class Schedule(ABC):
    """A pure next-run policy: ``reference instant -> next fire instant``.

    Implementations hold only static configuration and never read a clock or
    sleep. Subclasses implement `next_run_after`; the abstract base exists so the
    driver loop (the deferred process runner) can depend on the interface rather
    than a concrete schedule.
    """

    @abstractmethod
    def next_run_after(self, reference: datetime) -> datetime:
        """Return the next fire instant strictly after ``reference`` (tz-aware)."""
        raise NotImplementedError


@dataclass(frozen=True)
class IntervalSchedule(Schedule):
    """Fire every ``interval``, anchored to a fixed ``anchor`` instant.

    Slots are ``anchor + k*interval`` for integer ``k``; `next_run_after` returns
    the slot for the smallest ``k`` that is strictly after ``reference``. Anchoring
    (rather than naive ``reference + interval``) makes the fire times stable and
    reproducible: the same wall-clock slots recur no matter when the loop polls.

    ``anchor`` must be tz-aware; ``interval`` must be strictly positive.
    """

    interval: timedelta
    anchor: datetime

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValueError("interval must be strictly positive")
        _require_aware(self.anchor)

    def next_run_after(self, reference: datetime) -> datetime:
        _require_aware(reference)
        # Integer floor-division on timedeltas is exact (no float rounding), so a
        # reference sitting *exactly* on a slot yields that slot's index k; the
        # +1 advances past it — preserving strictly-after for any interval, not
        # only float-exact ones. (`reference < anchor` floors to a negative k,
        # correctly returning the anchor or an earlier slot as appropriate.)
        k = (reference - self.anchor) // self.interval + 1
        return self.anchor + k * self.interval


@dataclass(frozen=True)
class DailySchedule(Schedule):
    """Fire at fixed UTC times of day, e.g. 09:00 and 18:00 every day.

    ``times`` is a non-empty set of wall-clock times interpreted in **UTC**.
    `next_run_after` returns the earliest ``date@time`` (UTC) strictly after
    ``reference``, scanning today's remaining times then rolling to tomorrow.

    **UTC-only (v1).** A fixed-time-of-day schedule is inherently wall-clock, and
    honoring a civil timezone means handling DST gaps/overlaps. That is a
    deliberate deferral: pin to UTC now (matching ADR 0001), add a ``tz`` field as
    a follow-up. ``times`` carrying tzinfo is rejected to keep the contract honest.
    """

    times: tuple[time, ...]

    def __post_init__(self) -> None:
        if not self.times:
            raise ValueError("DailySchedule requires at least one time")
        if any(t.tzinfo is not None for t in self.times):
            raise ValueError("DailySchedule times must be naive (interpreted as UTC)")
        # Normalize: sorted + de-duplicated so the scan is order-independent.
        object.__setattr__(self, "times", tuple(sorted(set(self.times))))

    def next_run_after(self, reference: datetime) -> datetime:
        _require_aware(reference)
        ref_utc = reference.astimezone(UTC)
        # Scan today's slots, then tomorrow's first slot. Two days always suffice
        # because `times` is non-empty, so tomorrow's earliest is a guaranteed
        # upper bound strictly after any instant today.
        for day_offset in (0, 1):
            day = ref_utc.date() + timedelta(days=day_offset)
            for t in self.times:
                candidate = datetime.combine(day, t, tzinfo=UTC)
                if candidate > ref_utc:
                    return candidate
        # Unreachable: tomorrow's first slot is always strictly after `ref_utc`.
        raise AssertionError("DailySchedule failed to find a next slot")
