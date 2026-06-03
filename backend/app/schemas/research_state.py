"""Pydantic schemas for Deep Research state and provenance.

See `docs/adrs/0001-research-state-and-provenance.md` for the architectural
decisions behind this module (provenance pattern, ID scheme, datetime
semantics, mutability, strictness).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8). Hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous (no `_`/`-` from base64url
    # leaking into the random part). See ADR 0001 for the collision analysis.
    return f"{prefix}_{secrets.token_hex(8)}"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceType(StrEnum):
    WEB = "web"
    PDF = "pdf"
    PAPER = "paper"
    YOUTUBE = "youtube"
    REPO = "repo"
    FILE = "file"


_STRICT = ConfigDict(extra="forbid")


class Source(BaseModel):
    """A source discovered during the Knowledge Acquisition band."""

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("src"))
    url: str
    type: SourceType
    title: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_metadata: dict[str, str] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A parsed unit of content from a Source."""

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("chk"))
    source_id: str
    text: str
    position: int | None = None


class Evidence(BaseModel):
    """An extracted claim with attached, inline provenance.

    Per ADR 0001, each Evidence carries a self-contained snapshot of the
    source and chunk that backs it (source_url, chunk_text) so a state
    dump can be read without traversing the discovery registry. The
    snapshot duplicates fields on the corresponding Source and Chunk;
    that duplication is the deliberate cost of the attached pattern.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("ev"))
    claim: str
    source_id: str
    source_url: str
    chunk_id: str
    chunk_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extracted_via: str


class KnowledgeAcquisitionState(BaseModel):
    """State produced by the Knowledge Acquisition band of Deep Research."""

    model_config = _STRICT

    sources: list[Source] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class SubQuestion(BaseModel):
    """A decomposed question within a research plan.

    The order of sub-questions in `ResearchPlan.sub_questions` is the
    priority order (head = highest priority). An explicit `priority`
    field is deliberately omitted at v1 — order alone is sufficient
    and avoids the priority-vs-list-order ambiguity.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("sq"))
    text: str
    rationale: str | None = None


class ResearchPlan(BaseModel):
    """State produced by the Research Control band of Deep Research.

    The Plan decomposes the job topic into sub-questions and optionally
    refines the topic into a sharper goal statement. The original topic
    stays on `ResearchState.topic`; the Plan is the decomposition.

    Empty-vs-completed contract: `ResearchPlan()` (the default) represents
    the pre-planning state. A completed plan — emitted by the
    `ResearchPlannerAgent` (M3) — must contain at least one `SubQuestion`;
    an empty `sub_questions` list should be read as "planner has not run
    yet," not "planner produced nothing useful." The planner enforces this by
    raising `PlannerError` rather than emitting an empty plan, so a non-empty
    `sub_questions` list is the de-facto "planner ran" signal. An *explicit*
    band-status field (distinguishing running/failed/etc.) is deferred to the
    Orchestrator (M4); see ADR 0001 for the rationale behind keeping lifecycle
    distinctions out of the schema for v1.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("plan"))
    goal: str | None = None
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResearchState(BaseModel):
    """Canonical state container for a Deep Research workflow run.

    One instance per research job. Mutable by design (LangGraph nodes
    update state via return-new-state). See ADR 0001 for the rationale
    behind the container shape, mutability, and the empty-substate
    convention (no `None` defaults for band fields).
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("job"))
    topic: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Job-level failure reason, set when `status` becomes FAILED (ADR 0005).
    # Top-level lifecycle metadata, not a band substate — ADR 0001's
    # "no None defaults for band fields" rule does not apply here. A single
    # scalar is single-writer-safe under the linear graph; aggregating errors
    # from concurrent branches is topology-contingent and deferred with the
    # M5/M7 fan-out reducer decision (same class as ADR 0002 §6).
    error: str | None = None

    # Workflow order: plan first, then acquisition. Future band substates
    # (reasoning, publishing) slot in after acquisition in subsequent PRs.
    plan: ResearchPlan = Field(default_factory=ResearchPlan)
    acquisition: KnowledgeAcquisitionState = Field(
        default_factory=KnowledgeAcquisitionState,
    )
