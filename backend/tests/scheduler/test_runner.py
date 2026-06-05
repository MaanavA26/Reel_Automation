"""`BatchRunner` behavior (ADR 0034) — error isolation + concurrency cap.

Fully hermetic: produce callables are local fakes; the concurrency cap is forced
deterministically with `asyncio.Barrier`, so no real sleeping is needed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.scheduler.runner import BatchRunner


def test_empty_batch_returns_empty_result() -> None:
    async def produce(topic: str) -> str:
        return topic

    result = asyncio.run(BatchRunner(produce).run_batch([]))
    assert result.results == []
    assert result.succeeded == []
    assert result.failed == []


def test_results_preserve_submission_order() -> None:
    async def produce(topic: str) -> str:
        return topic.upper()

    result = asyncio.run(BatchRunner(produce, max_concurrency=4).run_batch(["a", "b", "c"]))
    assert [r.topic for r in result.results] == ["a", "b", "c"]
    assert [r.value for r in result.succeeded] == ["A", "B", "C"]


def test_one_failure_does_not_stop_the_batch() -> None:
    class Boom(RuntimeError):
        pass

    async def produce(topic: str) -> str:
        if topic == "bad":
            raise Boom("kaboom")
        return topic

    result = asyncio.run(BatchRunner(produce, max_concurrency=2).run_batch(["ok1", "bad", "ok2"]))
    assert [r.topic for r in result.succeeded] == ["ok1", "ok2"]
    assert len(result.failed) == 1
    failure = result.failed[0]
    assert failure.topic == "bad"
    assert isinstance(failure.error, Boom)
    assert failure.value is None
    assert not failure.ok


def test_concurrency_cap_is_enforced() -> None:
    """Peak in-flight count must equal the cap, never exceed it.

    With cap=2 and 4 topics, a correct semaphore lets exactly 2 enter; the
    `Barrier(2)` only trips once 2 are inside, then the next pair runs. A broken
    cap would let all 4 enter, drive the peak to 4, and fail this assertion.
    """
    cap = 2
    barrier = asyncio.Barrier(cap)
    state = {"active": 0, "peak": 0}

    async def produce(topic: str) -> str:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        # Block until `cap` tasks are simultaneously inside, proving the overlap.
        await barrier.wait()
        state["active"] -= 1
        return topic

    # Total is a multiple of cap so the barrier never deadlocks on a short party.
    topics = ["t0", "t1", "t2", "t3"]
    result = asyncio.run(BatchRunner(produce, max_concurrency=cap).run_batch(topics))

    assert state["peak"] == cap
    assert len(result.succeeded) == len(topics)


def test_max_concurrency_must_be_at_least_one() -> None:
    async def produce(topic: str) -> str:
        return topic

    with pytest.raises(ValueError, match=">= 1"):
        BatchRunner(produce, max_concurrency=0)
