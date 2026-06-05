"""Durable SQLite-backed `SqliteJobStore`: jobs survive process restarts.

The in-memory `JobStore` (see `store`) is single-process and **non-durable** — a
restart loses every job (ADR 0031's explicit deferral). This module ships the
durable backend ADR 0031 deferred: the same job-lifecycle service, but persisting
the canonical `ResearchState` to a SQLite database keyed by job id, so an enqueued
or completed job is still readable after the process is restarted.

**Same interface, same semantics.** `SqliteJobStore` satisfies the `JobStoreBackend`
protocol (see `base`) exactly as the in-memory `JobStore` does — `enqueue` mints a
``QUEUED`` job, `run` transitions it through ``RUNNING`` to a terminal state, and
`get` reads a snapshot or returns ``None``. The router and DI wiring are unchanged;
swapping backends is a composition-root concern, not an interface change.

**The persisted value is the canonical `ResearchState` JSON**, serialized via
`ResearchState.model_dump_json` and restored via `ResearchState.model_validate_json`
(the round-trip preserves aware-UTC timestamps and the nested band substates). The
job id is the state's own ``id`` — no parallel status column that could drift from
the workflow's own ``status``/``error`` (the same single-source-of-truth choice the
in-memory store makes; ADR 0031 §2).

**Single-process, single-connection, single-loop by design.** One long-lived
`sqlite3.Connection` is held for the store's lifetime and all access is serialized
by a single `asyncio.Lock`; every method runs on the event-loop thread (including
`run`, scheduled as a FastAPI background task), so the connection's default
``check_same_thread`` guard is satisfied without offloading. DB calls briefly block
the loop — acceptable at this scope (the same honesty the in-memory store applies to
its lock). The connection-per-call and `asyncio.to_thread` offload variants are
deferred; see ADR 0040. This store **is** durable across restarts but is still a
single-process design — a cross-worker/shared-state store (e.g. Postgres/Redis)
remains a later concern.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.research_state import JobStatus, ResearchState
from app.services.jobs.store import JobRunner

logger = logging.getLogger(__name__)

# One row per job: the opaque id is the primary key, the value is the canonical
# `ResearchState` JSON. Status/error live *inside* that JSON (mirrored verbatim
# from the workflow), deliberately not duplicated as columns that could drift.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL
)
"""


class SqliteJobStore:
    """Durable, SQLite-backed registry of Deep Research jobs, keyed by `ResearchState.id`.

    Interface-compatible with the in-memory `JobStore` (both satisfy
    `JobStoreBackend`) and lifecycle-identical: `enqueue` → ``QUEUED``, `run`
    transitions ``RUNNING`` → terminal, `get` reads or returns ``None``. Unlike the
    in-memory store it **survives process restarts** — a second `SqliteJobStore`
    opened on the same database path sees the jobs the first one persisted.

    All access is serialized by one `asyncio.Lock` over one long-lived connection on
    the event-loop thread (see the module docstring / ADR 0040).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Open (or create) the job database at ``db_path``.

        ``db_path`` is a filesystem path for durability (the production use); the
        ``":memory:"`` default keeps a bare instance cheap, but note an in-memory
        database is per-connection and therefore **not** durable across instances —
        durability requires a file path. The schema is created if absent, so the
        same path can be reopened across restarts.
        """
        self._db_path = str(db_path)
        # `check_same_thread` defaults to True; all access is on the event-loop
        # thread, so the default guard holds without offloading (ADR 0040).
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        self._lock = asyncio.Lock()

    def _read(self, job_id: str) -> ResearchState | None:
        """Load and deserialize a job row, or return ``None`` if absent.

        Synchronous DB helper; callers hold ``self._lock``.
        """
        row = self._conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return ResearchState.model_validate_json(row[0])

    def _write(self, state: ResearchState) -> None:
        """Insert or replace a job row with the serialized `ResearchState`.

        Synchronous DB helper; callers hold ``self._lock``.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO jobs (id, state) VALUES (?, ?)",
            (state.id, state.model_dump_json()),
        )
        self._conn.commit()

    async def enqueue(self, topic: str) -> ResearchState:
        """Register a new job in the ``QUEUED`` state and return its snapshot.

        Mirrors `JobStore.enqueue`: mints a fresh `ResearchState` (which assigns the
        opaque job ``id``), persists it, and returns it so the router can hand the
        ``QUEUED`` snapshot to the client and schedule `run` in the background.
        """
        state = ResearchState(topic=topic)
        async with self._lock:
            self._write(state)
        logger.info("enqueued research job %s", state.id)
        return state

    async def get(self, job_id: str) -> ResearchState | None:
        """Return the current snapshot for ``job_id``, or ``None`` if unknown.

        ``None`` is the not-found signal; mapping it to an HTTP 404 is the router's
        concern (this service stays transport-agnostic), exactly as the in-memory
        store.
        """
        async with self._lock:
            return self._read(job_id)

    async def run(self, job_id: str, runner: JobRunner) -> None:
        """Run a queued job to completion and persist its terminal state.

        Lifecycle-identical to `JobStore.run`: transition to ``RUNNING``, invoke
        ``runner``, and persist the terminal `ResearchState` verbatim — so the job's
        terminal ``status`` mirrors the workflow's own (which may itself be
        ``FAILED`` in-band). An *uncaught* runner exception is converted into a
        ``FAILED`` snapshot here (mirroring the workflow's `_with_failure_handling`)
        so a background failure is observable via `get`.

        The lock is held only around the DB reads/writes, never across the
        ``await runner`` call, so a long-running job does not block other access.
        """
        async with self._lock:
            current = self._read(job_id)
            if current is None:
                # Defensive: `run` is only ever scheduled right after `enqueue`,
                # so a missing record means a programming error upstream.
                logger.error("cannot run unknown research job %s", job_id)
                return
            running = current.model_copy(
                update={"status": JobStatus.RUNNING, "updated_at": datetime.now(UTC)}
            )
            self._write(running)

        try:
            terminal = await runner(current)
        except Exception as exc:  # record any runner failure as a terminal state
            logger.exception("research job %s failed", job_id)
            async with self._lock:
                self._write(
                    current.model_copy(
                        update={
                            "status": JobStatus.FAILED,
                            "error": f"{type(exc).__name__}: {exc}",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
            return

        async with self._lock:
            self._write(terminal)
        logger.info("research job %s finished with status %s", job_id, terminal.status)

    def close(self) -> None:
        """Close the underlying SQLite connection.

        Idempotent-friendly for orderly shutdown / test teardown; the store is
        unusable afterwards. Not part of the `JobStoreBackend` lifecycle protocol
        (the in-memory store has nothing to close) — a backend-specific affordance.
        """
        self._conn.close()
