"""Ordering guarantees for `TopicQueue` (ADR 0034) — FIFO and priority."""

from __future__ import annotations

import pytest

from app.scheduler.queue import TopicQueue


def test_fifo_by_default() -> None:
    q = TopicQueue()
    for t in ("a", "b", "c"):
        q.enqueue(t)
    assert q.drain() == ["a", "b", "c"]


def test_lower_priority_dequeued_first() -> None:
    q = TopicQueue()
    q.enqueue("normal")
    q.enqueue("urgent", priority=-1)
    q.enqueue("later", priority=5)
    assert q.drain() == ["urgent", "normal", "later"]


def test_equal_priority_breaks_ties_fifo() -> None:
    q = TopicQueue()
    # Same priority, enqueued in order — seq tiebreaker must preserve FIFO,
    # never fall through to comparing the topic strings.
    q.enqueue("zebra", priority=1)
    q.enqueue("apple", priority=1)
    assert q.drain() == ["zebra", "apple"]


def test_drain_respects_limit_and_leaves_remainder() -> None:
    q = TopicQueue()
    for t in ("a", "b", "c", "d"):
        q.enqueue(t)
    assert q.drain(limit=2) == ["a", "b"]
    assert len(q) == 2
    assert q.drain() == ["c", "d"]


def test_drain_more_than_available_returns_all() -> None:
    q = TopicQueue()
    q.enqueue("a")
    assert q.drain(limit=10) == ["a"]
    assert not q


def test_dequeue_empty_raises() -> None:
    with pytest.raises(IndexError):
        TopicQueue().dequeue()


def test_drain_negative_limit_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        TopicQueue().drain(limit=-1)


def test_len_and_bool() -> None:
    q = TopicQueue()
    assert not q
    assert len(q) == 0
    q.enqueue("a")
    assert q
    assert len(q) == 1
