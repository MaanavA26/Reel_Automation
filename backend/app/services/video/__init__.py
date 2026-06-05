"""End-to-end video band — topic → finished short-form video (ADR 0032).

The *linchpin* component (CLAUDE.md §1/§3): a deterministic `VideoPipeline`
*service* (CLAUDE.md §4 — orchestration of existing tools/agents, no new
judgment of its own) that chains the two finished subsystems into one path:

    topic → Deep Research (`run_research`) → `CreatorPacket`
          → Media Production (`MediaPipeline`) → `MediaPlan`
          → a `VideoArtifact` (the finished video's uri + metadata)

All collaborators are injected (the research `ResearchDeps`, the media
`MediaDeps`), so the pipeline is fully exercisable offline with the repo's Fake
providers and config-gated for a live render.
"""

from app.services.video.jobs import (
    VideoJob,
    VideoJobRunner,
    VideoJobStatus,
    VideoJobStore,
)
from app.services.video.pipeline import (
    VideoArtifact,
    VideoPipeline,
    VideoPipelineBundle,
    VideoPipelineError,
    build_video_pipeline,
)

__all__ = [
    "VideoArtifact",
    "VideoJob",
    "VideoJobRunner",
    "VideoJobStatus",
    "VideoJobStore",
    "VideoPipeline",
    "VideoPipelineBundle",
    "VideoPipelineError",
    "build_video_pipeline",
]
