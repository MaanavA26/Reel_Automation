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


class CritiqueDecision(StrEnum):
    """The Editorial Critic's verdict on a synthesis (M10). Binary at v1."""

    ACCEPT = "accept"
    REVISE = "revise"


class QualityIssueKind(StrEnum):
    """Model-authored class of a synthesis quality problem (M10)."""

    REDUNDANT = "redundant"  # findings restate the same point
    IMBALANCED = "imbalanced"  # a sub-question answered one-sidedly
    OVERSTATED = "overstated"  # prose overstates past disputed/weakest_support
    UNCLEAR = "unclear"  # statement is vague / not answer-shaped


class QualityIssue(BaseModel):
    """One model-authored quality problem with a synthesis, tied to ids (M10).

    The model authors ``kind`` + ``detail`` (prose) and references the affected
    findings/sub-questions only by *local index*; code resolves those to real ids
    (out-of-range dropped) and an issue resolving to nothing is dropped — it
    cannot be about anything that exists (§11, the M9 drop-empty guard). No id at
    v1 (a sub-unit of `Critique`).
    """

    model_config = _STRICT

    kind: QualityIssueKind
    detail: str
    finding_ids: list[str] = Field(default_factory=list)
    sub_question_ids: list[str] = Field(default_factory=list)


class Critique(BaseModel):
    """An editorial assessment of a `Synthesis` — the band's third-order judgment (M10).

    A `Verdict` judges evidence; a `Finding` composes verdicts; a `Critique`
    judges the *composition*. It is meta-inference, so the §11 boundary is
    enforced exactly as M8/M9: the model authors prose + local indices only;
    every id is code-attached/validated; and the *structural* fact (which
    sub-questions are uncovered) is **code-derived** — the model gets no field to
    report it. The accept/revise ``decision`` is likewise code-derived (REVISE
    iff any sub-question is uncovered OR any quality issue was raised), so the
    model cannot vote ACCEPT past an objective coverage gap. See ADR 0012.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("crit"))
    # code-derived structural facts (never model-authored):
    decision: CritiqueDecision
    uncovered_sub_question_ids: list[str] = Field(default_factory=list)
    # model-authored judgment (issue ids code-attached/validated):
    issues: list[QualityIssue] = Field(default_factory=list)
    rationale: str
    critiqued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    critiqued_via: str


class KnowledgeReasoningState(BaseModel):
    """State produced by the Knowledge Reasoning band of Deep Research.

    Carries the cross-checked `Verdict`s (M8), the `Synthesis` built on them (M9),
    and the editorial `Critique`s of that synthesis (M10). ``critiques`` is a list
    (not a single object): a `Critique` has required fields so it cannot be a
    ``default_factory`` default, and ``| None`` is barred by ADR 0001's
    no-None-defaults-for-band-fields rule — the empty list is the "critic has not
    run" signal, and it gives the M10b revision loop a per-iteration audit trail.
    Same empty-substate convention as the other bands (ADR 0001).
    """

    model_config = _STRICT

    verdicts: list[Verdict] = Field(default_factory=list)
    synthesis: Synthesis = Field(default_factory=Synthesis)
    critiques: list[Critique] = Field(default_factory=list)


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

    # Number of synthesis attempts performed, incremented once per Editorial
    # Critic pass (M10b). Top-level lifecycle metadata like `error` (not a band
    # substate, so ADR 0001's no-None-defaults rule does not apply), and placed
    # top-level — *not* inside `reasoning` — so the synthesize node's `reasoning`
    # channel rewrite on the revision back-edge cannot re-zero it. The critique
    # node is its single writer. It bounds the `critique -> synthesize` cycle
    # against a cap so the loop always terminates (ADR 0012).
    revision_iteration: int = 0

    # Workflow order: plan -> acquisition -> reasoning. The publishing substate
    # slots in after reasoning in a subsequent PR (M11-M12).
    plan: ResearchPlan = Field(default_factory=ResearchPlan)
    acquisition: KnowledgeAcquisitionState = Field(
        default_factory=KnowledgeAcquisitionState,
    )
    reasoning: KnowledgeReasoningState = Field(
        default_factory=KnowledgeReasoningState,
    )
