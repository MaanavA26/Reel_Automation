"""In-memory `VideoJobStore`: enqueue, run, and address video jobs by id.

The video-band analogue of `app.services.jobs.JobStore` (ADR 0031), backing the
async video surface (`POST /videos/jobs` + `GET /videos/jobs/{id}`). It keeps the
videos router thin (CLAUDE.md §10): the router enqueues and reads snapshots while
*all* job-lifecycle logic lives here.

Unlike the research store — whose job record is the canonical `ResearchState`
itself (it already carries id/status/error) — a finished video has no such
all-in-one state object: the terminal artifact is a `VideoArtifact`, but a
``QUEUED``/``RUNNING``/``FAILED`` job has no artifact yet. So this store wraps a
small typed `VideoJob` envelope (id + status + optional artifact + optional
error). It is the same single-process, non-durable, `asyncio.Lock`-guarded model
ADR 0031 documents — a restart resets it, and a durable/cross-worker store is
deferred.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.services.video.pipeline import VideoArtifact, _gen_id

logger = logging.getLogger(__name__)


class VideoJobStatus(StrEnum):
    """Lifecycle state of a video job (mirrors `schemas.research_state.JobStatus`)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoJob(BaseModel):
    """A video job's snapshot: id + status + (on completion) the artifact.

    Strict + id-prefixed like the repo's other DTOs. ``artifact`` is populated on
    ``COMPLETED``; ``error`` on ``FAILED``; both are ``None`` while the job is
    ``QUEUED``/``RUNNING``. The same payload serves status polling and the result
    read (it carries the artifact once done).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _gen_id("vjob"))
    topic: str
    status: VideoJobStatus = VideoJobStatus.QUEUED
    artifact: VideoArtifact | None = None
    error: str | None = None


# A job runner takes a topic and resolves to the terminal artifact. Typed as a
# callable (rather than importing `VideoPipeline`) so the store stays free of a
# pipeline dependency — the orchestration logic is pipeline-agnostic, mirroring
# `app.services.jobs.JobRunner`.
VideoJobRunner = Callable[[str], Awaitable[VideoArtifact]]


class VideoJobStore:
    """Process-local registry of video jobs, keyed by `VideoJob.id`.

    Not durable and not cross-worker (see the module docstring / ADR 0031). All
    mutations are serialized by a single `asyncio.Lock`.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, VideoJob] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, topic: str) -> VideoJob:
        """Register a new job in ``QUEUED`` and return its snapshot."""
        job = VideoJob(topic=topic)
        async with self._lock:
            self._jobs[job.id] = job
        logger.info("enqueued video job %s", job.id)
        return job

    async def get(self, job_id: str) -> VideoJob | None:
        """Return the current snapshot for ``job_id``, or ``None`` if unknown."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def run(self, job_id: str, runner: VideoJobRunner) -> None:
        """Run a queued job to completion and record its terminal snapshot.

        Invoked as a FastAPI background task. Transitions to ``RUNNING``, invokes
        ``runner`` (the pipeline), and records the terminal artifact; any runner
        exception is converted into a ``FAILED`` snapshot here (mirroring the
        research store) so a background failure stays observable via `get`.
        """
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                logger.error("cannot run unknown video job %s", job_id)
                return
            self._jobs[job_id] = current.model_copy(update={"status": VideoJobStatus.RUNNING})

        try:
            artifact = await runner(current.topic)
        except Exception as exc:
            logger.exception("video job %s failed", job_id)
            async with self._lock:
                self._jobs[job_id] = current.model_copy(
                    update={
                        "status": VideoJobStatus.FAILED,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            return

        async with self._lock:
            self._jobs[job_id] = current.model_copy(
                update={"status": VideoJobStatus.COMPLETED, "artifact": artifact}
            )
        logger.info("video job %s completed", job_id)
