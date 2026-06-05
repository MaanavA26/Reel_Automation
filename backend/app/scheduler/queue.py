"""`TopicQueue` — the ordered backlog of topics to produce.

A deterministic *tool* (CLAUDE.md §4): no reasoning, just ordered storage of the
topics the batch loop will turn into videos. It supports two disciplines from one
structure:

* **FIFO** — with all priorities equal (the default), topics come out in the order
  they went in.
* **Priority** — a lower ``priority`` integer is dequeued first; ties break FIFO.

Ordering is implemented over `heapq` with a ``(priority, seq, topic)`` key, where
``seq`` is a monotonic insertion counter. The ``seq`` tiebreaker is load-bearing:
without it, equal-priority entries would fall through to comparing the *topic
strings*, which is neither FIFO nor meaningful. Single-event-loop / single-thread
use is assumed (like `JobStore`); no internal locking.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class QueuedTopic:
    """A topic plus its ordering key. ``topic`` is excluded from comparison.

    Comparison is by ``(priority, seq)`` only — `heapq` never compares the topic
    text (two topics could be equal-priority + equal-seq only if mis-constructed,
    which the queue's monotonic counter prevents). Lower ``priority`` sorts first;
    ``seq`` preserves FIFO within a priority.
    """

    priority: int
    seq: int
    topic: str = field(compare=False)


class TopicQueue:
    """An ordered backlog of topics, FIFO by default, priority-aware on request.

    Not thread-safe and not durable — a process-local backlog mirroring the
    single-process assumption of `JobStore` (ADR 0031). Lower ``priority`` values
    are produced first; equal priorities preserve enqueue order.
    """

    def __init__(self) -> None:
        self._heap: list[QueuedTopic] = []
        self._seq: int = 0

    def enqueue(self, topic: str, *, priority: int = 0) -> None:
        """Add ``topic`` to the backlog.

        ``priority`` defaults to ``0``; pass a *lower* value to jump ahead of the
        existing default-priority backlog, a *higher* value to fall behind it.
        """
        heapq.heappush(self._heap, QueuedTopic(priority=priority, seq=self._seq, topic=topic))
        self._seq += 1

    def dequeue(self) -> str:
        """Remove and return the next topic. Raises `IndexError` if empty."""
        if not self._heap:
            raise IndexError("dequeue from an empty TopicQueue")
        return heapq.heappop(self._heap).topic

    def drain(self, limit: int | None = None) -> list[str]:
        """Pop up to ``limit`` topics in order (all of them if ``limit`` is None).

        The convenience the batch loop uses to pull one batch's worth of work in a
        single call. ``limit`` must be non-negative when provided.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        count = len(self._heap) if limit is None else min(limit, len(self._heap))
        return [self.dequeue() for _ in range(count)]

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)
