"""In-memory `JobStore`: enqueue, run, and address Deep Research jobs by id.

This service backs the async research surface (`POST /research/jobs` +
`GET /research/jobs/{id}`). It keeps the router thin (CLAUDE.md §10): the router
enqueues and reads snapshots, while *all* job-lifecycle logic — minting the
QUEUED record, transitioning to RUNNING, invoking the workflow, and recording the
terminal state — lives here.

**The job record is the canonical `ResearchState` itself**, not a parallel
envelope. `ResearchState` already carries everything a job needs — an opaque
``id`` (preserved end-to-end through the graph's partial-update contract), a
``status`` (``QUEUED``/``RUNNING``/``COMPLETED``/``FAILED``), and an ``error`` —
so reusing it avoids a duplicate status field that could drift and keeps this
service out of ``schemas/`` (CLAUDE.md scope: schemas are owned elsewhere). The
job id is the state's own ``id``.

**Single-process, non-durable by design.** Jobs live in a plain dict in this
process's memory, guarded by one `asyncio.Lock`. That is sufficient for the
single-worker development/demo target and for hermetic tests, but it is **not**
durable (a restart loses all jobs) and **not** cross-worker (a job enqueued on
one worker is invisible to another). A durable, shared-state store (e.g. Redis or
a database) is deferred to a later milestone — see ADR 0031. The store is held as
a process-singleton on ``app.state`` (see `app.main`), never rebuilt per request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.schemas.research_state import JobStatus, ResearchState

logger = logging.getLogger(__name__)

# A job runner is anything that takes an initial `ResearchState` and resolves to
# the terminal one. In production this is a thin closure over the injected
# `run_research` + `ResearchDeps`; tests inject a fake runner directly. Typing it
# as a callable (rather than importing `run_research`) keeps this service free of
# a workflow dependency — the orchestration logic is workflow-agnostic.
JobRunner = Callable[[ResearchState], Awaitable[ResearchState]]


class JobStore:
    """Process-local registry of Deep Research jobs, keyed by `ResearchState.id`.

    Not durable and not cross-worker (see the module docstring / ADR 0031). All
    mutations are serialized by a single `asyncio.Lock`; the store runs on one
    event loop, so the lock is for honesty/future-proofing rather than to tame
    real contention.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ResearchState] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, topic: str) -> ResearchState:
        """Register a new job in the ``QUEUED`` state and return its snapshot.

        Mints a fresh `ResearchState` (which assigns the opaque job ``id``),
        records it, and returns it. The caller (the router) hands the returned
        snapshot back to the client immediately and schedules `run` as a
        background task — so the client sees ``QUEUED`` with a usable id before
        the workflow does any work.
        """
        state = ResearchState(topic=topic)
        async with self._lock:
            self._jobs[state.id] = state
        logger.info("enqueued research job %s", state.id)
        return state

    async def get(self, job_id: str) -> ResearchState | None:
        """Return the current snapshot for ``job_id``, or ``None`` if unknown.

        ``None`` is the not-found signal; translating it into an HTTP 404 is the
        router's concern (this service stays transport-agnostic).
        """
        async with self._lock:
            return self._jobs.get(job_id)

    async def run(self, job_id: str, runner: JobRunner) -> None:
        """Run a queued job to completion and record its terminal state.

        Invoked as a FastAPI background task. Transitions the job to ``RUNNING``,
        invokes ``runner`` (the workflow), and stores the terminal
        `ResearchState` verbatim — so the job's terminal ``status`` mirrors the
        workflow's own (which may itself be ``FAILED`` in-band, e.g. an exhausted
        node). Any *uncaught* exception from the runner is converted into a
        ``FAILED`` snapshot here (mirroring the workflow's `_with_failure_handling`
        contract) so a background failure is observable via `get` rather than
        lost to the event loop.
        """
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                # Defensive: `run` is only ever scheduled right after `enqueue`,
                # so a missing record means a programming error upstream.
                logger.error("cannot run unknown research job %s", job_id)
                return
            self._jobs[job_id] = current.model_copy(
                update={"status": JobStatus.RUNNING, "updated_at": datetime.now(UTC)}
            )

        try:
            terminal = await runner(current)
        except Exception as exc:  # record any runner failure as a terminal state
            logger.exception("research job %s failed", job_id)
            async with self._lock:
                self._jobs[job_id] = current.model_copy(
                    update={
                        "status": JobStatus.FAILED,
                        "error": f"{type(exc).__name__}: {exc}",
                        "updated_at": datetime.now(UTC),
                    }
                )
            return

        async with self._lock:
            self._jobs[job_id] = terminal
        logger.info("research job %s finished with status %s", job_id, terminal.status)
