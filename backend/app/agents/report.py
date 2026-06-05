"""Report agent — composes the reasoning output into a structured research report.

An *agent* (judgment, CLAUDE.md §4): given the synthesized `Finding`s it authors
the report's narrative prose — a title, an abstract, and sections that compose
findings into an answer-shaped narrative. It opens the Research Publishing band
(§5.5 band D), producing the engine's final, most-polished, most-downstream
artifact.

The agent/tool split: the *prose* is judgment (this agent); the *bibliography*
and the *caveats* are deterministic and code-owned (the `services/publishing/`
tools), so a published report can never cite a source the model invented nor bury
a contradiction. The §11 boundary is held one layer past M9/M10:

1. The model references findings only by *local index* (``F#``) into the numbered
   list it was shown; code resolves them to real `Finding` ids (out-of-range
   dropped), and a section resolving to zero real findings is dropped. Each
   section's ``sub_question_ids`` are derived in code from the cited findings — a
   single model index space, so the M9 two-index hazard cannot arise.
2. ``citations`` are assembled by walking the real provenance chain
   (`assemble_citations`); ``caveats`` are derived from the *full* findings set +
   the last critique (`derive_caveats`). The model authors neither — they are
   non-omittable by construction.

Single ``LONG_CONTEXT`` model call over the already-reduced findings set (report
writing is long-form summarization — the same honest role label as M9 synthesis;
not the raw-evidence grouping ADR 0010 rejected). See ADR 0017.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.schemas.research_state import (
    Finding,
    KnowledgeAcquisitionState,
    KnowledgeReasoningState,
    Report,
    ReportSection,
    ResearchPlan,
)
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter
from app.services.publishing.caveats import derive_caveats
from app.services.publishing.citations import assemble_citations

logger = logging.getLogger(__name__)


class ReportError(RuntimeError):
    """Raised on empty findings input, or when no report section survives."""


SYSTEM_PROMPT = (
    "You are a research report writer. You are given a research goal, its "
    "sub-questions, and the synthesized findings (numbered F0, F1, …), each with "
    "its support strength (whether it is disputed or single-source). Write a "
    "structured report: a concise title, an executive abstract, and a set of "
    "sections that compose the findings into a clear narrative answering the "
    "goal. For each section give a heading, the narrative prose, and the findings "
    "it draws on (by their F-number). Reflect disputed or single-source support "
    "honestly in the wording — do not present a contradicted finding as settled. "
    "Use only the provided findings; reference them only by their F-number. Do "
    "not write a limitations/caveats section or a bibliography — the engine "
    "assembles those from the grounded data."
)


class _SectionDraft(BaseModel):
    """Model-output shape for one section (local indices only — no ids)."""

    heading: str
    narrative: str
    findings: list[int] = Field(default_factory=list)  # F# → synthesis.findings


class _ReportOutput(BaseModel):
    """Structured output of the single report call (prose + local indices only)."""

    title: str
    abstract: str
    sections: list[_SectionDraft] = Field(default_factory=list)


class ReportAgent:
    """Composes a `Report` from the reasoning output via the ``LONG_CONTEXT`` model."""

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def generate(
        self,
        plan: ResearchPlan,
        reasoning: KnowledgeReasoningState,
        acquisition: KnowledgeAcquisitionState,
    ) -> Report:
        """Generate the research `Report` from the reasoning + acquisition state.

        Raises `ReportError` if there are no findings (never publish on empty —
        synthesize raises upstream, so this is a defensive wiring guard) or if no
        section survives id-resolution (the call failed or every section cited
        only unresolvable findings). A thin/disputed report is **not** a failure:
        it ships with prominent code-derived caveats.
        """
        findings = reasoning.synthesis.findings
        if not findings:
            raise ReportError("report generation received no findings")

        model = self._router.for_role(ModelRole.LONG_CONTEXT)
        published_via = f"report:{model.model}"

        output = await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(plan, reasoning),
            schema=_ReportOutput,
        )

        sections: list[ReportSection] = []
        cited_finding_ids: set[str] = set()
        for draft in output.sections:
            section = self._build_section(draft, findings)
            if section is not None:
                sections.append(section)
                cited_finding_ids.update(section.finding_ids)

        if not sections:
            raise ReportError("report generation produced no resolvable sections")

        # Citations cover only the *cited* findings (the report's references);
        # caveats cover the *full* findings set so an uncited disputed finding
        # still surfaces (ADR 0017).
        cited_findings = [f for f in findings if f.id in cited_finding_ids]
        latest_critique = reasoning.critiques[-1] if reasoning.critiques else None
        citations = assemble_citations(
            cited_findings, reasoning.verdicts, acquisition.evidence, acquisition.sources
        )
        caveats = derive_caveats(findings, latest_critique)

        return Report(
            title=output.title,
            abstract=output.abstract,
            sections=sections,
            citations=citations,
            caveats=caveats,
            published_via=published_via,
        )

    @staticmethod
    def _build_section(draft: _SectionDraft, findings: list[Finding]) -> ReportSection | None:
        """Resolve a section draft's local finding indices to a `ReportSection`.

        Out-of-range indices are dropped + logged; a section citing no real
        finding is dropped (the M9 drop-empty guard). ``sub_question_ids`` is the
        ordered union of the cited findings' sub-questions (code-derived).
        """
        resolved: list[Finding] = []
        seen_idx: set[int] = set()
        for i in draft.findings:
            if 0 <= i < len(findings):
                if i not in seen_idx:
                    resolved.append(findings[i])
                    seen_idx.add(i)
            else:
                logger.warning("report: dropping out-of-range finding index %s", i)
        if not resolved:
            logger.warning("report: dropping section with no valid findings: %s", draft.heading)
            return None

        sub_question_ids: list[str] = []
        for f in resolved:
            for sq_id in f.sub_question_ids:
                if sq_id not in sub_question_ids:
                    sub_question_ids.append(sq_id)

        return ReportSection(
            heading=draft.heading,
            narrative=draft.narrative,
            finding_ids=[f.id for f in resolved],
            sub_question_ids=sub_question_ids,
        )

    @staticmethod
    def _build_prompt(plan: ResearchPlan, reasoning: KnowledgeReasoningState) -> str:
        goal = plan.goal or "(no refined goal provided)"
        sub_qs = "\n".join(f"- {sq.text}" for sq in plan.sub_questions)
        finding_lines = "\n".join(
            f"[F{i}] ({'disputed' if f.disputed else f.weakest_support.value}) {f.statement}"
            for i, f in enumerate(reasoning.synthesis.findings)
        )
        return (
            f"Research goal:\n{goal}\n\n"
            f"Sub-questions:\n{sub_qs or '(none)'}\n\n"
            f"Synthesized findings:\n{finding_lines}\n\n"
            "Write the structured report (title, abstract, sections grounded in the findings)."
        )
