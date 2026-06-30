"""Structured, DB-ready per-run artifact + log sink (ADR 0057).

A deterministic *tool* layer (CLAUDE.md §4 — no LLM, no judgment): it persists,
per research/video run, the flat record set a future structured DB can bulk-load,
and offers a thin per-stage structured-event helper over the app's JSON logger.

Public surface:

- `RunArtifactSink` (the `Protocol` seam) and `FileRunArtifactSink` (the
  process-local production default that writes JSON under the gitignored
  ``backend/runs/`` dir) — `sink`. A durable DB backend is a documented follow-up
  that implements the same `Protocol`, mirroring the `JobStore`/`ChannelStore`
  deferral (ADR 0040 / ADR 0042).
- The flat record models + their pure projectors from a `ResearchState` /
  `MediaPlan` — `records`.
- `log_stage_event` — the metadata-only per-stage event emitter — `events`.
"""

from app.services.runlog.events import log_stage_event
from app.services.runlog.sink import (
    FileRunArtifactSink,
    RunArtifactSink,
)

__all__ = [
    "FileRunArtifactSink",
    "RunArtifactSink",
    "log_stage_event",
]
