"""Flat, DB-ready record shapes for a persisted research/video run.

A deterministic *tool* (CLAUDE.md §4 — no LLM, no judgment): a pure projection
layer that flattens the nested `ResearchState` (and the optional media tail) into
**row-shaped** records, one Pydantic model per artifact *kind*. Each record
carries the owning ``run_id`` (the future foreign key) so a downstream bulk-load
maps kind → table and ``run_id`` → FK without re-deriving anything.

Why a projection rather than dumping `ResearchState` verbatim
-------------------------------------------------------------
`ResearchState.model_dump_json()` is a single deeply-nested blob — the right
choice for the job store's "the state *is* the record" decision (ADR 0040 §2),
the wrong shape for a structured DB. A relational/columnar target wants flat rows
with stable scalar keys, so this module restates each band's artifacts as flat
records that stamp the ``run_id`` and lift the most query-useful fields to the top
level while retaining the original ids for re-join to the full provenance chain.

The records deliberately do **not** re-snapshot the full provenance graph (e.g. a
`VerdictRecord` keeps its evidence ids as a list, not the embedded `Evidence`
objects). The graph already lives in the canonical state; these records are the
query-friendly *view*, and the sink also persists the canonical state alongside
them for lossless re-hydration.

All timestamps are the models' own aware-UTC values, serialized to ISO-8601 via
``model_dump(mode="json")`` (Pydantic renders aware datetimes as ISO strings) —
the projection never mints a write-time timestamp into a record, which keeps it a
pure function of its inputs (deterministic, round-trippable).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.media.pipeline import MediaPlan
from app.schemas.research_state import ResearchState

_STRICT = ConfigDict(extra="forbid")


class RunRecord(BaseModel):
    """One row per run — the top-level lifecycle/identity record.

    The parent row a future schema's other kinds reference by ``run_id``. Carries
    the job topic, terminal status, the revision-loop count, and the run's own
    aware-UTC ``created_at`` / ``updated_at`` (never a sink write-time clock).
    ``error`` is the job-level failure reason verbatim from the state (already
    leak-scrubbed at its source per ADR 0043); the records dir is gitignored, so
    persisting it here is in-contract.
    """

    model_config = _STRICT

    run_id: str
    topic: str
    status: str
    revision_iteration: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class SubQuestionRecord(BaseModel):
    """One row per planned sub-question (priority = list order, ADR 0001)."""

    model_config = _STRICT

    run_id: str
    plan_id: str
    sub_question_id: str
    text: str
    rationale: str | None
    position: int


class SourceRecord(BaseModel):
    """One row per discovered source (Knowledge Acquisition band)."""

    model_config = _STRICT

    run_id: str
    source_id: str
    url: str
    type: str
    title: str | None
    discovered_via: str
    discovered_at: datetime


class EvidenceRecord(BaseModel):
    """One row per extracted, source-grounded claim with inline provenance."""

    model_config = _STRICT

    run_id: str
    evidence_id: str
    claim: str
    source_id: str
    source_url: str
    chunk_id: str
    confidence: float
    extracted_via: str
    extracted_at: datetime


class VerdictRecord(BaseModel):
    """One row per cross-checked verdict (Knowledge Reasoning band).

    Evidence ids stay as lists (the re-join keys); the graph itself lives in the
    canonical state the sink persists alongside these records.
    """

    model_config = _STRICT

    run_id: str
    verdict_id: str
    claim: str
    support_level: str
    confidence: float
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    verified_via: str
    verified_at: datetime


class FindingRecord(BaseModel):
    """One row per synthesized finding, with its code-derived grounding summary."""

    model_config = _STRICT

    run_id: str
    finding_id: str
    statement: str
    detail: str | None
    sub_question_ids: list[str]
    supporting_verdict_ids: list[str]
    disputed: bool
    weakest_support: str
    synthesized_via: str
    synthesized_at: datetime


class ReportRecord(BaseModel):
    """One row per published report (sections flattened into ``ReportSectionRecord``)."""

    model_config = _STRICT

    run_id: str
    report_id: str
    title: str
    abstract: str
    section_count: int
    citation_count: int
    caveat_count: int
    published_via: str
    published_at: datetime


class ReportSectionRecord(BaseModel):
    """One row per report section, anchored to the plan by sub-question id."""

    model_config = _STRICT

    run_id: str
    report_id: str
    section_id: str
    heading: str
    narrative: str
    finding_ids: list[str]
    sub_question_ids: list[str]
    position: int


class CreatorPacketRecord(BaseModel):
    """One row per creator packet — the band-D handoff header (counts + re-join key)."""

    model_config = _STRICT

    run_id: str
    packet_id: str
    report_id: str
    hook_count: int
    angle_count: int
    narrative_count: int
    key_fact_count: int
    warning_count: int
    published_via: str
    created_at: datetime


class HookRecord(BaseModel):
    """One row per opening-hook idea (creator packet)."""

    model_config = _STRICT

    run_id: str
    packet_id: str
    text: str
    finding_ids: list[str]
    position: int


class AngleRecord(BaseModel):
    """One row per content angle (creator packet)."""

    model_config = _STRICT

    run_id: str
    packet_id: str
    angle: str
    rationale: str
    finding_ids: list[str]
    position: int


class NarrativeRecord(BaseModel):
    """One row per short-form narrative option, with its full beat-by-beat outline."""

    model_config = _STRICT

    run_id: str
    packet_id: str
    title: str
    script_outline: str
    finding_ids: list[str]
    position: int


class MediaRecord(BaseModel):
    """One row for the produced media tail — narration script + render metadata.

    Present only when a `MediaPlan` is persisted (the video path). Carries the
    chosen narrative title, the full narration script (joined beats), the rendered
    video uri/dimensions/duration, and the re-join key back to the source packet.
    """

    model_config = _STRICT

    run_id: str
    media_plan_id: str
    source_packet_id: str
    narrative_title: str
    script: str
    script_segments: list[str]
    audio_id: str
    audio_uri: str
    audio_duration_ms: int
    caption_track_id: str
    video_id: str
    video_uri: str
    video_duration_ms: int
    video_width: int
    video_height: int
    produced_via: str


def run_record(state: ResearchState) -> RunRecord:
    """Project the run's top-level lifecycle/identity into a `RunRecord`."""
    return RunRecord(
        run_id=state.id,
        topic=state.topic,
        status=state.status.value,
        revision_iteration=state.revision_iteration,
        error=state.error,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def sub_question_records(state: ResearchState) -> list[SubQuestionRecord]:
    """Flatten the plan's sub-questions (list order = priority) into records."""
    return [
        SubQuestionRecord(
            run_id=state.id,
            plan_id=state.plan.id,
            sub_question_id=sq.id,
            text=sq.text,
            rationale=sq.rationale,
            position=i,
        )
        for i, sq in enumerate(state.plan.sub_questions)
    ]


def source_records(state: ResearchState) -> list[SourceRecord]:
    """Flatten the acquisition band's discovered sources into records."""
    return [
        SourceRecord(
            run_id=state.id,
            source_id=s.id,
            url=s.url,
            type=s.type.value,
            title=s.title,
            discovered_via=s.discovered_via,
            discovered_at=s.discovered_at,
        )
        for s in state.acquisition.sources
    ]


def evidence_records(state: ResearchState) -> list[EvidenceRecord]:
    """Flatten the acquisition band's extracted evidence into records."""
    return [
        EvidenceRecord(
            run_id=state.id,
            evidence_id=e.id,
            claim=e.claim,
            source_id=e.source_id,
            source_url=e.source_url,
            chunk_id=e.chunk_id,
            confidence=e.confidence,
            extracted_via=e.extracted_via,
            extracted_at=e.extracted_at,
        )
        for e in state.acquisition.evidence
    ]


def verdict_records(state: ResearchState) -> list[VerdictRecord]:
    """Flatten the reasoning band's cross-checked verdicts into records."""
    return [
        VerdictRecord(
            run_id=state.id,
            verdict_id=v.id,
            claim=v.claim,
            support_level=v.support_level.value,
            confidence=v.confidence,
            supporting_evidence_ids=list(v.supporting_evidence_ids),
            contradicting_evidence_ids=list(v.contradicting_evidence_ids),
            verified_via=v.verified_via,
            verified_at=v.verified_at,
        )
        for v in state.reasoning.verdicts
    ]


def finding_records(state: ResearchState) -> list[FindingRecord]:
    """Flatten the synthesis band's findings into records."""
    return [
        FindingRecord(
            run_id=state.id,
            finding_id=f.id,
            statement=f.statement,
            detail=f.detail,
            sub_question_ids=list(f.sub_question_ids),
            supporting_verdict_ids=list(f.supporting_verdict_ids),
            disputed=f.disputed,
            weakest_support=f.weakest_support.value,
            synthesized_via=f.synthesized_via,
            synthesized_at=f.synthesized_at,
        )
        for f in state.reasoning.synthesis.findings
    ]


def report_records(state: ResearchState) -> list[ReportRecord]:
    """Flatten the publishing band's reports (headers) into records."""
    return [
        ReportRecord(
            run_id=state.id,
            report_id=r.id,
            title=r.title,
            abstract=r.abstract,
            section_count=len(r.sections),
            citation_count=len(r.citations),
            caveat_count=len(r.caveats),
            published_via=r.published_via,
            published_at=r.published_at,
        )
        for r in state.publishing.reports
    ]


def report_section_records(state: ResearchState) -> list[ReportSectionRecord]:
    """Flatten every report's sections (list order preserved) into records."""
    return [
        ReportSectionRecord(
            run_id=state.id,
            report_id=r.id,
            section_id=sec.id,
            heading=sec.heading,
            narrative=sec.narrative,
            finding_ids=list(sec.finding_ids),
            sub_question_ids=list(sec.sub_question_ids),
            position=i,
        )
        for r in state.publishing.reports
        for i, sec in enumerate(r.sections)
    ]


def creator_packet_records(state: ResearchState) -> list[CreatorPacketRecord]:
    """Flatten the publishing band's creator packets (headers) into records."""
    return [
        CreatorPacketRecord(
            run_id=state.id,
            packet_id=p.id,
            report_id=p.report_id,
            hook_count=len(p.hooks),
            angle_count=len(p.angles),
            narrative_count=len(p.narratives),
            key_fact_count=len(p.key_facts),
            warning_count=len(p.warnings),
            published_via=p.published_via,
            created_at=p.created_at,
        )
        for p in state.publishing.packets
    ]


def hook_records(state: ResearchState) -> list[HookRecord]:
    """Flatten every packet's hook ideas into records."""
    return [
        HookRecord(
            run_id=state.id,
            packet_id=p.id,
            text=h.text,
            finding_ids=list(h.finding_ids),
            position=i,
        )
        for p in state.publishing.packets
        for i, h in enumerate(p.hooks)
    ]


def angle_records(state: ResearchState) -> list[AngleRecord]:
    """Flatten every packet's content angles into records."""
    return [
        AngleRecord(
            run_id=state.id,
            packet_id=p.id,
            angle=a.angle,
            rationale=a.rationale,
            finding_ids=list(a.finding_ids),
            position=i,
        )
        for p in state.publishing.packets
        for i, a in enumerate(p.angles)
    ]


def narrative_records(state: ResearchState) -> list[NarrativeRecord]:
    """Flatten every packet's narrative options (with outlines) into records."""
    return [
        NarrativeRecord(
            run_id=state.id,
            packet_id=p.id,
            title=n.title,
            script_outline=n.script_outline,
            finding_ids=list(n.finding_ids),
            position=i,
        )
        for p in state.publishing.packets
        for i, n in enumerate(p.narratives)
    ]


def media_record(run_id: str, plan: MediaPlan) -> MediaRecord:
    r"""Project the produced `MediaPlan` (script + render metadata) into a record.

    The narration ``script`` is the joined beats (``"\n".join(script_segments)``,
    the same narration the media pipeline synthesized), persisted alongside the
    rendered-video metadata and the re-join key to the source packet.
    """
    return MediaRecord(
        run_id=run_id,
        media_plan_id=plan.id,
        source_packet_id=plan.source_packet_id,
        narrative_title=plan.narrative_title,
        script="\n".join(plan.script_segments),
        script_segments=list(plan.script_segments),
        audio_id=plan.audio.id,
        audio_uri=plan.audio.audio_uri,
        audio_duration_ms=plan.audio.duration_ms,
        caption_track_id=plan.captions.id,
        video_id=plan.video.id,
        video_uri=plan.video.video_uri,
        video_duration_ms=plan.video.duration_ms,
        video_width=plan.video.width,
        video_height=plan.video.height,
        produced_via=plan.produced_via,
    )
