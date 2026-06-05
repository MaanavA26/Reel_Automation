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
    """A source discovered during the Knowledge Acquisition band.

    `discovered_via` records *how* the source was found (e.g. ``"search:fake"``,
    ``"search:tavily"``). It is first-class provenance, symmetric with
    `Evidence.extracted_via`, and is the machine-readable encoding of the
    evidence-vs-inference distinction (CLAUDE.md §11): a `Source` is always
    tool-discovered, never minted by an LLM. See ADR 0006.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("src"))
    url: str
    type: SourceType
    discovered_via: str
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


class SupportLevel(StrEnum):
    """How a claim is supported once cross-checked across sources (M8).

    A purely *structural* axis: how many distinct sources back the claim and
    whether they conflict. Claim *strength* (thin vs strong support) is carried
    separately by `Verdict.confidence`, so the two orthogonal dimensions never
    collapse into one lossy label. ``CORROBORATED`` is defined as **two or more
    distinct sources** agreeing — that distinct-source count is code-derived,
    never model-counted (see ADR 0010 / `CrossVerificationAgent`).
    """

    CORROBORATED = "corroborated"  # >=2 distinct sources agree
    SINGLE_SOURCE = "single_source"  # supported, but by one source only
    CONTRADICTED = "contradicted"  # sources conflict


class Verdict(BaseModel):
    """A cross-checked claim — the Knowledge Reasoning band's unit of *inference*.

    Distinct in kind from `Evidence`: an `Evidence` is a source-grounded *fact*
    (what a chunk says); a `Verdict` is a *judgment about* a group of evidence
    (whether sources agree). The two live in different substates so downstream
    bands can never conflate inference with primary fact (CLAUDE.md §11).

    A `Verdict` references its evidence **by id** into
    `KnowledgeAcquisitionState.evidence` — it does not re-snapshot
    ``chunk_text``/``source_url`` (the inverse of `Evidence`'s attached pattern,
    correct because `Evidence` is already self-documenting; ADR 0001 anticipated
    reasoning-band by-id cross-references). The model authors only ``claim`` /
    ``support_level`` / ``confidence``; every id and the provenance are
    code-attached and code-validated against the real evidence set (§11 made
    structural). See ADR 0010.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("vd"))
    claim: str  # canonical/merged claim across the corroborating evidence
    support_level: SupportLevel
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verified_via: str


class Finding(BaseModel):
    """A synthesized answer-unit — the synthesis band's second-order inference (M9).

    A `Verdict` judges *one cluster of evidence* (do sources agree?). A `Finding`
    composes *multiple verdicts* into an answer addressed to the research plan
    (what does the cross-checked corpus say about a sub-question?). It is
    inference built on inference, so the §11 boundary is enforced exactly as for
    `Verdict`: the model authors prose only; every id is code-attached and
    code-validated against the real `Verdict`/`SubQuestion` sets, and the
    grounding summary (``disputed`` / ``weakest_support``) is **code-derived**
    from the cited verdicts — the model is given no field to self-report it. A
    `Finding` can therefore never cite a verdict the model invented, nor overstate
    its grounding past what its verdicts support. See ADR 0011.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("fnd"))
    # model-authored prose:
    statement: str
    detail: str | None = None
    # code-attached id references (resolved from local indices, validated):
    sub_question_ids: list[str] = Field(default_factory=list)
    supporting_verdict_ids: list[str] = Field(default_factory=list)
    # code-derived grounding summary (never model-authored):
    disputed: bool  # True iff any cited verdict is CONTRADICTED
    weakest_support: SupportLevel  # floor over the cited verdicts' support levels
    synthesized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    synthesized_via: str


class Synthesis(BaseModel):
    """The synthesis band's output container (M9).

    Holds the plan-anchored `Finding`s — each links the `SubQuestion`(s) it
    addresses, so M10 (Editorial Critic) can check coverage structurally and M11
    can render answer-by-question. An emergent narrative layer (cross-cutting
    summary, key takeaways) is deliberately **deferred** to its real consumer
    (M11/M12 publishing): it would be ungrounded model prose, the very
    self-report this band is built to deny, so it waits for a consumer rather
    than shipping speculatively (cf. deferred `Chunk.parsed_via`, ADR 0008).

    No id: a band substate (symmetric with the other bands' substates), not a
    first-class artifact. Empty ``findings`` reads as "synthesis has not run."
    """

    model_config = _STRICT

    findings: list[Finding] = Field(default_factory=list)


class KnowledgeReasoningState(BaseModel):
    """State produced by the Knowledge Reasoning band of Deep Research.

    Carries the cross-checked `Verdict`s (M8) and the `Synthesis` built on them
    (M9). Gap analysis and revision artifacts (M10) slot in here as additional
    fields in their owning milestone — same empty-substate convention as the
    other bands (ADR 0001): an empty ``verdicts`` / ``synthesis.findings`` reads
    as "that step has not run," not "it produced nothing."
    """

    model_config = _STRICT

    verdicts: list[Verdict] = Field(default_factory=list)
    synthesis: Synthesis = Field(default_factory=Synthesis)


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

    # Workflow order: plan -> acquisition -> reasoning. The publishing substate
    # slots in after reasoning in a subsequent PR (M11-M12).
    plan: ResearchPlan = Field(default_factory=ResearchPlan)
    acquisition: KnowledgeAcquisitionState = Field(
        default_factory=KnowledgeAcquisitionState,
    )
    reasoning: KnowledgeReasoningState = Field(
        default_factory=KnowledgeReasoningState,
    )
