"""Hermetic tests for the in-memory `JobStore` (the store wired in `app.main`).

`JobStore` is process-local and non-durable by design (ADR 0031), so unlike the
`SqliteJobStore` suite there is nothing to round-trip across a restart and no
connection to `close()`. These tests cover the lifecycle that *is* its contract:
enqueue → get round-trip, the RUNNING→terminal transitions, the
runner-exception → FAILED path (`store.py`'s `except Exception`), an in-band
FAILED state stored verbatim, and unknown-id behavior. Async methods are driven
with `asyncio.run` (the repo's convention for this offline suite — no
`pytest-asyncio` dependency).
"""

from __future__ import annotations

import asyncio

from app.schemas.research_state import JobStatus, ResearchState
from app.services.jobs import JobStore


def test_enqueue_returns_queued_snapshot() -> None:
    store = JobStore()
    state = asyncio.run(store.enqueue("quantum computing"))

    assert state.topic == "quantum computing"
    assert state.status is JobStatus.QUEUED
    assert state.id.startswith("job_")


def test_enqueue_then_get_round_trips_the_state() -> None:
    store = JobStore()
    enqueued = asyncio.run(store.enqueue("astrophysics"))

    fetched = asyncio.run(store.get(enqueued.id))

    # In-memory: `get` hands back the very snapshot that was enqueued.
    assert fetched == enqueued


def test_get_unknown_id_returns_none() -> None:
    store = JobStore()
    assert asyncio.run(store.get("job_does_not_exist")) is None


def test_run_records_completed_terminal_state() -> None:
    store = JobStore()
    job = asyncio.run(store.enqueue("photosynthesis"))

    async def runner(initial: ResearchState) -> ResearchState:
        # The intermediate RUNNING transition is recorded before the runner is
        # awaited (the lock is released first), so it is observable mid-flight.
        mid = await store.get(job.id)
        assert mid is not None and mid.status is JobStatus.RUNNING
        return initial.model_copy(update={"status": JobStatus.COMPLETED})

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.COMPLETED


def test_run_converts_runner_exception_to_failed() -> None:
    store = JobStore()
    job = asyncio.run(store.enqueue("black holes"))

    async def runner(_: ResearchState) -> ResearchState:
        raise RuntimeError("boom")

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.FAILED
    assert final.error == "RuntimeError: boom"


def test_run_persists_in_band_failure_verbatim() -> None:
    """A runner that *returns* a FAILED state (in-band) is stored as-is, not masked."""
    store = JobStore()
    job = asyncio.run(store.enqueue("fusion energy"))

    async def runner(initial: ResearchState) -> ResearchState:
        return initial.model_copy(update={"status": JobStatus.FAILED, "error": "node exhausted"})

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.FAILED
    assert final.error == "node exhausted"


def test_run_unknown_id_is_a_noop() -> None:
    store = JobStore()
    called = False

    async def runner(initial: ResearchState) -> ResearchState:
        nonlocal called
        called = True
        return initial

    asyncio.run(store.run("job_missing", runner))

    assert called is False  # runner never invoked for an unknown id
    assert asyncio.run(store.get("job_missing")) is None
