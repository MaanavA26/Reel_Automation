"""A tiny, dependency-free timing harness for the perf benchmarks.

Stdlib ``time.perf_counter`` only — deliberately *not* ``pytest-benchmark``
(adding it would conflict with a sibling branch's ``pyproject``; see the package
README/CHANGELOG). The harness is a deterministic *tool* (CLAUDE.md §4): it runs
a callable a fixed number of times and reports the best, mean, and worst wall
time, then renders a fixed-width table. No judgment, no I/O beyond the returned
string, no network.

"Best" (minimum) is reported as the headline statistic because it is the least
noisy estimate of intrinsic cost — a run is only ever slowed by scheduling /
GC / cache effects, never sped up below the true floor, so the minimum is the
closest observable to the real per-call work. Mean and worst are kept for
visibility into variance.

The harness is intentionally not a pass/fail gate: it has no thresholds. The
benchmarks that use it print this table for a human to read; see the module
docstrings in ``test_*`` for why they are deselected from the default suite.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

_T = TypeVar("_T")


@dataclass(frozen=True)
class TimingResult:
    """The wall-time statistics for one benchmarked callable.

    Times are in seconds (``time.perf_counter`` units). ``label`` names the
    measured case for the rendered table.
    """

    label: str
    repeats: int
    best_s: float
    mean_s: float
    worst_s: float

    @property
    def best_ms(self) -> float:
        """Best (minimum) time in milliseconds."""
        return self.best_s * 1_000.0


def time_callable(
    label: str,
    fn: Callable[[], _T],
    *,
    repeats: int = 5,
    warmup: int = 1,
) -> tuple[TimingResult, _T]:
    """Time ``fn`` over ``repeats`` runs (after ``warmup`` untimed runs).

    Returns the aggregated :class:`TimingResult` and the *last* return value of
    ``fn`` (so a caller can also assert the result is structurally sane). Each
    call is timed independently with ``time.perf_counter`` and the per-call
    deltas are aggregated; ``warmup`` runs prime caches / lazy imports and are
    discarded. ``repeats`` must be >= 1.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    for _ in range(max(warmup, 0)):
        fn()

    durations: list[float] = []
    last: _T | None = None
    for _ in range(repeats):
        start = time.perf_counter()
        last = fn()
        durations.append(time.perf_counter() - start)

    # ``last`` is bound because ``repeats >= 1`` guarantees the loop ran.
    result = TimingResult(
        label=label,
        repeats=repeats,
        best_s=min(durations),
        mean_s=statistics.fmean(durations),
        worst_s=max(durations),
    )
    return result, last  # type: ignore[return-value]


def render_table(title: str, results: list[TimingResult]) -> str:
    """Render timing results as a fixed-width text table.

    Pure string formatting — the caller decides where to emit it (the benchmarks
    push it through the pytest terminal reporter so it is visible even on a
    passing, output-capturing run).
    """
    header = ("case", "repeats", "best (ms)", "mean (ms)", "worst (ms)")
    rows = [
        (
            r.label,
            str(r.repeats),
            f"{r.best_s * 1_000.0:.3f}",
            f"{r.mean_s * 1_000.0:.3f}",
            f"{r.worst_s * 1_000.0:.3f}",
        )
        for r in results
    ]

    widths = [
        max(len(header[col]), *(len(row[col]) for row in rows)) if rows else len(header[col])
        for col in range(len(header))
    ]

    def _fmt_row(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[col]) for col, cell in enumerate(cells))

    sep = "  ".join("-" * w for w in widths)
    lines = [f"\n{title}", _fmt_row(header), sep, *(_fmt_row(row) for row in rows)]
    return "\n".join(lines)
