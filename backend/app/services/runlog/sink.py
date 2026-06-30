"""`RunArtifactSink` seam + the file-backed production implementation.

A deterministic *tool* (CLAUDE.md §4 — no LLM, no judgment): persists, per
research/video run, the **flat, DB-ready record set** projected by
:mod:`app.services.runlog.records` so it can later be bulk-loaded into a free
structured database. It owns storage and serialization only; every reasoning step
already happened upstream in the research agents.

Protocol-first seam, mirroring `ChannelStore`/`JobStoreBackend`
---------------------------------------------------------------
`RunArtifactSink` is a `@runtime_checkable` `Protocol` so a future durable backend
(e.g. a ``SqlRunArtifactSink`` or a columnar loader) drops in behind the same
contract without touching callers — the exact "capability now, durable backend
later" deferral `JobStore` (ADR 0040) and `ChannelStore` (ADR 0042) make.
`FileRunArtifactSink` is the production default: it writes one JSON file per
artifact *kind* under ``<base_dir>/<run_id>/`` plus the canonical `ResearchState`
for lossless re-hydration. The output dir is **injected** (the caller passes the
gitignored ``backend/runs/`` root, ADR 0057), keeping the sink pure/relocatable
and the tests hermetic (``tmp_path``).

Async by design (sync IO inside)
--------------------------------
The method is async (mirroring `ChannelStore`, which justifies async by "the
deferred durable backend, which would be async") so adopting a real DB later is
not a signature-breaking change. The file writes inside are synchronous — the
repo's established sync-IO-inside-async stance (ADR 0040 §4).

Determinism
-----------
The sink mints **no** write-time timestamps into the records — every timestamp is
the source artifact's own aware-UTC value, serialized to ISO-8601 by
``model_dump(mode="json")``. Two writes of the same `ResearchState` therefore
produce byte-identical files (the only non-determinism, the random artifact ids,
lives on the state, not the sink), which is what makes the round-trip test exact.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.media.pipeline import MediaPlan
from app.schemas.research_state import ResearchState
from app.services.runlog import records

logger = logging.getLogger(__name__)

# The canonical, lossless `ResearchState` blob (ADR 0040 §2 round-trip), persisted
# alongside the flat records so a run can be re-hydrated into the full schema.
STATE_FILE = "state.json"

# One file per artifact *kind*; the basename (sans ``.json``) is the future table
# name. Each maps to the records-module projector that yields its row list. A
# single mapping is the one source of truth for "which kinds the sink writes".
_ARTIFACT_KINDS: dict[str, object] = {
    "sub_questions": records.sub_question_records,
    "sources": records.source_records,
    "evidence": records.evidence_records,
    "verdicts": records.verdict_records,
    "findings": records.finding_records,
    "reports": records.report_records,
    "report_sections": records.report_section_records,
    "creator_packets": records.creator_packet_records,
    "hooks": records.hook_records,
    "angles": records.angle_records,
    "narratives": records.narrative_records,
}

# The single-record run header (one row, not a list) gets its own file so the
# parent kind reads as one object rather than a one-element array.
RUN_FILE = "run.json"
MEDIA_FILE = "media.json"


@runtime_checkable
class RunArtifactSink(Protocol):
    """Persist a run's flat artifact record set, addressable by ``run_id``.

    The contract every backend (the file default, a deferred durable one, a test
    double) implements. Async so the seam survives a future I/O-bound DB backend
    unchanged. ``write`` is idempotent per ``run_id`` (a re-write overwrites the
    run's records), so a retried persist does not duplicate rows.
    """

    async def write(self, state: ResearchState, *, media_plan: MediaPlan | None = None) -> None:
        """Persist the run's artifacts (and the media tail when supplied)."""
        ...


def _dump_records(rows: list[BaseModel]) -> list[dict[str, object]]:
    """Serialize a homogeneous record list to JSON-mode dicts (ISO timestamps)."""
    return [row.model_dump(mode="json") for row in rows]


class FileRunArtifactSink:
    """Process-local `RunArtifactSink` that writes JSON files under a gitignored dir.

    The production default. Given a final `ResearchState` (and optionally the
    produced `MediaPlan`), it writes ``<base_dir>/<run_id>/`` containing:

    - ``run.json`` — the single run-header record;
    - one ``<kind>.json`` per artifact kind (a JSON array of flat records);
    - ``media.json`` — the narration script + render metadata (only when a
      `MediaPlan` is supplied);
    - ``state.json`` — the canonical `ResearchState` for lossless re-hydration.

    ``base_dir`` is injected (the caller passes the gitignored ``backend/runs/``
    root, or a ``tmp_path`` in tests), so the sink is pure and relocatable.
    """

    name = "file"

    def __init__(self, base_dir: Path) -> None:
        """Bind the sink to a base output directory (created lazily on first write)."""
        self._base_dir = Path(base_dir)

    @property
    def base_dir(self) -> Path:
        """The root directory under which per-run subdirectories are written."""
        return self._base_dir

    def run_dir(self, run_id: str) -> Path:
        """Return the per-run subdirectory path for ``run_id`` (not created here)."""
        return self._base_dir / run_id

    async def write(self, state: ResearchState, *, media_plan: MediaPlan | None = None) -> None:
        """Persist the run's flat records + canonical state under ``<base>/<run_id>/``.

        Idempotent per run id: each file is overwritten, so a retried persist of
        the same run replaces its records rather than appending. Empty bands write
        an empty JSON array (an explicit "this band produced nothing" row set,
        symmetric with the empty-substate convention of ADR 0001) — the kind file
        always exists, which keeps a downstream loader's table set stable.
        """
        run_dir = self.run_dir(state.id)
        run_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(run_dir / RUN_FILE, records.run_record(state).model_dump(mode="json"))

        for filename, projector in _ARTIFACT_KINDS.items():
            rows = projector(state)  # type: ignore[operator]
            self._write_json(run_dir / f"{filename}.json", _dump_records(rows))

        if media_plan is not None:
            media = records.media_record(state.id, media_plan)
            self._write_json(run_dir / MEDIA_FILE, media.model_dump(mode="json"))

        # The canonical state last: a lossless re-hydration source next to the
        # query-friendly records (ADR 0040 §2 round-trip discipline).
        self._write_json(run_dir / STATE_FILE, json.loads(state.model_dump_json()))

        logger.info("persisted run artifacts for %s under %s", state.id, run_dir)

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        """Write ``payload`` as pretty, sorted-key UTF-8 JSON (stable, diffable).

        ``sort_keys=True`` + ``ensure_ascii=False`` make the output deterministic
        and human-legible; a trailing newline keeps the file POSIX-clean.
        """
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
