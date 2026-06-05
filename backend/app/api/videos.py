"""End-to-end video API endpoints (ADR 0032).

A deliberately **thin** router (CLAUDE.md §10): it validates the request,
delegates to the `VideoPipeline` (which orchestrates research → creator packet →
media → finished video), and returns the typed artifact. All wiring lives in
`app.api.deps` / `app.services.composition`; all logic lives in the pipeline and
the subsystems it chains. The router owns only the HTTP contract.

Two surfaces, mirroring the research router:

* ``POST /videos`` runs the pipeline **synchronously** and returns the
  `VideoArtifact` — simple but blocks for the (slow) full run.
* ``POST /videos/jobs`` (202) enqueues a job and runs it as a background task;
  ``GET /videos/jobs/{id}`` polls the `VideoJob` snapshot (status + artifact on
  completion). Lifecycle bookkeeping lives in the `VideoJobStore` (ADR 0031's
  single-process, non-durable model).

Wiring failures (no live model/search/TTS backend configured) raise
`CompositionError`, which `app.main` maps to a 503 app-wide — so the videos
router inherits the legible "config problem, not bad request" status for free.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_video_job_store, get_video_pipeline
from app.services.video import VideoArtifact, VideoJob, VideoJobStore, VideoPipeline

router = APIRouter(prefix="/videos", tags=["videos"])


class VideoJobRequest(BaseModel):
    """Payload to request a short-form video for a topic.

    Kept inline in the router (not in `schemas/`) since it is a small, API-local
    contract. ``narrative_index`` selects which of the creator packet's narrative
    options to render (deterministic selection; ranking is upstream judgment).
    """

    topic: str = Field(
        min_length=1, description="The topic, question, or theme to turn into a video."
    )
    narrative_index: int = Field(
        default=0,
        ge=0,
        description="Which creator-packet narrative option to render.",
    )


@router.post(
    "",
    response_model=VideoArtifact,
    status_code=status.HTTP_200_OK,
    summary="Create a short-form video for a topic and return the artifact",
)
async def create_video(
    request: VideoJobRequest,
    pipeline: Annotated[VideoPipeline, Depends(get_video_pipeline)],
) -> VideoArtifact:
    """Run the full topic → finished video path and return the `VideoArtifact`.

    Synchronous: the response carries the rendered video's uri + metadata. A
    research run that fails or yields no narratable packet raises
    `VideoPipelineError` (500, an honest server-side failure); a wiring problem
    raises `CompositionError` (503).
    """
    return await pipeline.create(request.topic, narrative_index=request.narrative_index)


@router.post(
    "/jobs",
    response_model=VideoJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a video job and return immediately with its QUEUED id",
)
async def enqueue_video_job(
    request: VideoJobRequest,
    background_tasks: BackgroundTasks,
    pipeline: Annotated[VideoPipeline, Depends(get_video_pipeline)],
    store: Annotated[VideoJobStore, Depends(get_video_job_store)],
) -> VideoJob:
    """Register a job, schedule it to run in the background, and return at once.

    The async counterpart to `create_video`: returns ``202`` with the ``QUEUED``
    `VideoJob` (carrying the id the client polls) and runs the pipeline in a
    FastAPI background task. ``pipeline`` is resolved here via `Depends` (so test
    overrides apply) and closed over by the scheduled task.
    """
    job = await store.enqueue(request.topic)

    async def _run(topic: str) -> VideoArtifact:
        return await pipeline.create(topic, narrative_index=request.narrative_index)

    background_tasks.add_task(store.run, job.id, _run)
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=VideoJob,
    status_code=status.HTTP_200_OK,
    summary="Read a video job's current status / artifact by id",
)
async def get_video_job(
    job_id: str,
    store: Annotated[VideoJobStore, Depends(get_video_job_store)],
) -> VideoJob:
    """Return the job's current `VideoJob` snapshot, or 404 if unknown.

    The snapshot's ``status`` is the live job state; on completion the same
    payload carries the `VideoArtifact`, so this one endpoint serves both status
    polling and the result read.
    """
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no video job with id {job_id!r}",
        )
    return job
