"""Deep Research API endpoints.

A deliberately **thin** router (CLAUDE.md §10): it validates the request,
delegates to the workflow entrypoint (`run_research`) with an injected
`ResearchDeps` bundle, and returns the typed terminal `ResearchState`. All
wiring lives in `app.api.deps` / `app.services.composition`; all reasoning lives
in the agents/workflow. The router owns only the HTTP contract.

v1 runs the job **synchronously** (await `run_research`): the response carries
the terminal state, so it doubles as both "submit" and "read result/status".
Background execution, streaming progress, and an id-addressable
``GET /research/{id}`` status endpoint all require a job store and are deferred
to a later milestone (see ADR 0016).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import get_job_store, get_research_deps
from app.schemas.research_state import ResearchState
from app.services.jobs import JobStore
from app.workflows.deep_research import DEFAULT_MAX_SYNTHESES, ResearchDeps, run_research

router = APIRouter(prefix="/research", tags=["research"])


class ResearchJobRequest(BaseModel):
    """Payload to submit a Deep Research job.

    The *response* is the canonical `ResearchState` (returned verbatim; in
    synchronous v1 the terminal state carries both status and results), so only
    the request shape is defined here. Kept inline in the router (not in
    `schemas/`) since it is a small, API-local contract that mirrors the
    workflow's own ``max_syntheses`` knob.
    """

    topic: str = Field(min_length=1, description="The topic, question, or theme to research.")
    max_syntheses: int = Field(
        default=DEFAULT_MAX_SYNTHESES,
        ge=1,
        le=10,
        description="Max synthesis attempts before the revision loop is forced to terminate.",
    )


@router.post(
    "",
    response_model=ResearchState,
    status_code=status.HTTP_200_OK,
    summary="Submit a research job and return its terminal state",
)
async def submit_research_job(
    request: ResearchJobRequest,
    deps: Annotated[ResearchDeps, Depends(get_research_deps)],
) -> ResearchState:
    """Run a Deep Research job to completion and return the final typed state.

    Synchronous v1: the returned `ResearchState` is the terminal state, so its
    ``status`` and band substates are the job result. A run that exhausts its
    revision budget still completes (it is not an HTTP error); a node-level
    failure is reported in-band via ``status=failed`` + ``error`` rather than as
    an HTTP 5xx, preserving the typed contract.
    """
    initial = ResearchState(topic=request.topic)
    return await run_research(initial, deps=deps, max_syntheses=request.max_syntheses)


@router.post(
    "/jobs",
    response_model=ResearchState,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a research job and return immediately with its QUEUED id",
)
async def enqueue_research_job(
    request: ResearchJobRequest,
    background_tasks: BackgroundTasks,
    deps: Annotated[ResearchDeps, Depends(get_research_deps)],
    store: Annotated[JobStore, Depends(get_job_store)],
) -> ResearchState:
    """Register a job, schedule it to run in the background, and return at once.

    The async counterpart to `submit_research_job`: instead of blocking the
    request for the run's full duration, this returns ``202`` with the
    ``QUEUED`` `ResearchState` (carrying the id the client polls), and runs
    `run_research` in a FastAPI **background task**. Lifecycle bookkeeping lives
    in the `JobStore` (CLAUDE.md §10) — the router only enqueues and schedules.

    ``deps`` is resolved here via `Depends` (so test overrides apply) and closed
    over by the scheduled task; it is deliberately *not* rebuilt inside the task,
    where the override would not reach it.
    """
    job = await store.enqueue(request.topic)

    async def _run(initial: ResearchState) -> ResearchState:
        return await run_research(initial, deps=deps, max_syntheses=request.max_syntheses)

    background_tasks.add_task(store.run, job.id, _run)
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=ResearchState,
    status_code=status.HTTP_200_OK,
    summary="Read a research job's current status / result by id",
)
async def get_research_job(
    job_id: str,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> ResearchState:
    """Return the job's current `ResearchState` snapshot, or 404 if unknown.

    The snapshot's ``status`` is the live job state (``QUEUED``/``RUNNING``/
    ``COMPLETED``/``FAILED``); on completion the same payload carries the full
    result, so this one endpoint serves both status polling and result reads.
    """
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no research job with id {job_id!r}",
        )
    return job


# Composition failures (e.g. no production search/model adapter wired yet) are
# config/wiring problems, not bad requests — surface them as 503 rather than a
# bare 500 so the cause is legible to a caller. Registered on the app in
# `app.main` (FastAPI dispatches handlers over dependency-resolution errors too).
async def composition_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Translate a `CompositionError` into a 503 with the underlying detail.

    Typed ``exc: Exception`` to satisfy Starlette's handler signature without a
    ``type: ignore``; it is only ever registered for `CompositionError`, so the
    runtime type is narrow.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )
