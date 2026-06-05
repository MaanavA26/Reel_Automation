"""Scheduler / unattended batch-runner band — produce N videos/day without a human.

This package is the automation seam CLAUDE.md §3.4 reserves for "performance
optimization / orchestration fabric": the loop that turns a backlog of topics into
videos on a recurring cadence, with no human in the loop. It is built from three
deterministic, independently-testable *tools* (CLAUDE.md §4 — scheduling and
batch execution are procedural, not reasoning) that compose into the N/day loop:

* `Schedule` (`schedule.py`) — **pure** next-run computation. `IntervalSchedule`
  and `DailySchedule` answer "given this instant, when do we fire next?" with no
  clock and no sleeping, so the timing logic is unit-testable without waiting.
* `TopicQueue` (`queue.py`) — the ordered backlog (FIFO, priority-aware).
* `BatchRunner` (`runner.py`) — runs an **injected** ``Produce`` coroutine across a
  batch of topics under a concurrency cap, isolating per-topic failures. Decoupled
  from the real `VideoPipeline` (a sibling component) via the injected callable.

**The driver loop + real wiring (ADR 0054).** The long-lived process that
actually *runs* the loop — ``while not stopped: wait until schedule.next_run_after(
now()); await run_once()`` — lives in `closed_loop.py` (`ClosedLoopRunner`). It is
the only piece that touches a real clock and real sleeping (both **injected**, so
it stays hermetically testable), and it is where the injected ``Produce`` is bound
to the real `VideoPipeline`, the `PrePublishGate`, the human-review gate, the
publishing fabric, and the analytics feedback loop. Keeping it split from the three
pure primitives is what kept them hermetic; ADR 0054 realizes the deferral ADR 0034
reserved. The loop body (`run_once`) is itself clock-free; only the thin
`run_forever` wrapper binds the timing seam.
"""

from app.scheduler.closed_loop import (
    ClosedLoopRunner,
    LoopMode,
    PendingPublication,
    PendingPublicationStore,
    StopSignal,
    TickReport,
)
from app.scheduler.queue import QueuedTopic, TopicQueue
from app.scheduler.runner import BatchResult, BatchRunner, Produce, TopicResult
from app.scheduler.schedule import (
    DailySchedule,
    IntervalSchedule,
    Schedule,
)

__all__ = [
    "BatchResult",
    "BatchRunner",
    "ClosedLoopRunner",
    "DailySchedule",
    "IntervalSchedule",
    "LoopMode",
    "PendingPublication",
    "PendingPublicationStore",
    "Produce",
    "QueuedTopic",
    "Schedule",
    "StopSignal",
    "TickReport",
    "TopicQueue",
    "TopicResult",
]
