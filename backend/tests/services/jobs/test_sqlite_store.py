"""Hermetic tests for the durable `SqliteJobStore` (ADR 0040).

All tests are offline and use a real on-disk SQLite file under ``tmp_path`` — an
in-memory (``":memory:"``) database is per-connection and so cannot demonstrate the
store's whole reason for existing (cross-instance durability across a restart), so a
file path is used throughout. Async methods are driven with `asyncio.run` (the
repo's convention for this offline suite — no `pytest-asyncio` dependency).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.schemas.research_state import JobStatus, ResearchState
from app.services.jobs import JobStore, JobStoreBackend, SqliteJobStore


def _store(tmp_path: Path) -> SqliteJobStore:
    return SqliteJobStore(tmp_path / "jobs.db")


def test_enqueue_returns_queued_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = asyncio.run(store.enqueue("quantum computing"))

    assert state.topic == "quantum computing"
    assert state.status is JobStatus.QUEUED
    assert state.id.startswith("job_")
    store.close()


def test_enqueue_then_get_round_trips_the_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    enqueued = asyncio.run(store.enqueue("astrophysics"))

    fetched = asyncio.run(store.get(enqueued.id))

    # Full canonical round-trip: serialized → deserialized → structurally equal,
    # including aware-UTC timestamps and the nested band substates.
    assert fetched == enqueued
    store.close()


def test_get_unknown_id_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert asyncio.run(store.get("job_does_not_exist")) is None
    store.close()


def test_run_records_completed_terminal_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = asyncio.run(store.enqueue("photosynthesis"))

    async def runner(initial: ResearchState) -> ResearchState:
        # The intermediate RUNNING transition is persisted before the runner is
        # awaited (the lock is released first), so it is observable mid-flight.
        mid = await store.get(job.id)
        assert mid is not None and mid.status is JobStatus.RUNNING
        return initial.model_copy(update={"status": JobStatus.COMPLETED})

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.COMPLETED
    store.close()


def test_run_converts_runner_exception_to_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = asyncio.run(store.enqueue("black holes"))

    async def runner(_: ResearchState) -> ResearchState:
        raise RuntimeError("boom")

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.FAILED
    assert final.error == "RuntimeError: boom"
    store.close()


def test_run_persists_in_band_failure_verbatim(tmp_path: Path) -> None:
    """A runner that *returns* a FAILED state (in-band) is stored as-is, not masked."""
    store = _store(tmp_path)
    job = asyncio.run(store.enqueue("fusion energy"))

    async def runner(initial: ResearchState) -> ResearchState:
        return initial.model_copy(update={"status": JobStatus.FAILED, "error": "node exhausted"})

    asyncio.run(store.run(job.id, runner))

    final = asyncio.run(store.get(job.id))
    assert final is not None
    assert final.status is JobStatus.FAILED
    assert final.error == "node exhausted"
    store.close()


def test_run_unknown_id_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    called = False

    async def runner(initial: ResearchState) -> ResearchState:
        nonlocal called
        called = True
        return initial

    asyncio.run(store.run("job_missing", runner))

    assert called is False  # runner never invoked for an unknown id
    assert asyncio.run(store.get("job_missing")) is None
    store.close()


def test_jobs_survive_a_new_store_on_the_same_path(tmp_path: Path) -> None:
    """The durability guarantee: a fresh store on the same path sees prior jobs."""
    db_path = tmp_path / "jobs.db"

    first = SqliteJobStore(db_path)
    job = asyncio.run(first.enqueue("durable topic"))

    async def runner(initial: ResearchState) -> ResearchState:
        return initial.model_copy(update={"status": JobStatus.COMPLETED})

    asyncio.run(first.run(job.id, runner))
    first.close()  # simulate process exit

    # A brand-new store instance (a "restarted process") on the same file.
    second = SqliteJobStore(db_path)
    restored = asyncio.run(second.get(job.id))

    assert restored is not None
    assert restored.id == job.id
    assert restored.topic == "durable topic"
    assert restored.status is JobStatus.COMPLETED
    second.close()


def test_both_backends_satisfy_the_protocol(tmp_path: Path) -> None:
    """Both stores are interchangeable behind `JobStoreBackend` (backward-compat)."""
    sqlite_store = SqliteJobStore(tmp_path / "jobs.db")
    assert isinstance(sqlite_store, JobStoreBackend)
    assert isinstance(JobStore(), JobStoreBackend)
    sqlite_store.close()
