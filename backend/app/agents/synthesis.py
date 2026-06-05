"""Synthesis agent — composes cross-checked Verdicts into plan-anchored Findings.

An *agent* (judgment, CLAUDE.md §4): given the research plan's sub-questions and
the `Verdict`s produced by cross-verification (M8), it synthesizes `Finding`s —
answer-units that compose multiple verdicts into a statement addressed to the
plan. This is the synthesis step of the Knowledge Reasoning band (§5.5): a
*second-order* inference (built on verdicts, which are themselves inference on
evidence).

It is a single model call over the already-reduced verdict set — not the
per-item fan-out of extraction/verification, and not the raw-evidence grouping
ADR 0010 rejected: verdicts are bounded (≈one per claim cluster) and synthesis is
inherently holistic (a coherent answer spans verdicts), so the model needs to see
them together. No deterministic blocking tool is warranted (there is no
combinatorial explosion to bound, unlike M8).

The §11 evidence-vs-inference boundary is made structural the same way M8 did,
applied one layer up:

1. The model references verdicts and sub-questions only by *local index* into the
   numbered lists it was shown (``V#`` / ``S#``); code resolves those indices to
   real `Verdict`/`SubQuestion` ids and drops any out of range, so a `Finding`
   can never cite something the model invented. The two index spaces are
   separate DTO fields resolved against separate lists, so a verdict index can
   never be misread as a sub-question.
2. The grounding summary (``disputed`` / ``weakest_support``) is **code-derived**
   from the cited verdicts' `support_level` — the model is given no field to
   self-report it. A finding therefore cannot overstate its grounding (present a
   contradicted verdict as settled): the caveat is carried forward
   non-omittably for the downstream report.

See ADR 0011.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, Field

from app.schemas.research_state import (
    Critique,
    Finding,
    ResearchPlan,
    SupportLevel,
    Synthesis,
    Verdict,
)
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class SynthesisError(RuntimeError):
    """Raised on empty verdict input, or when no finding is produced from it."""


# Severity ordering for the ``weakest_support`` floor: most-cautious wins, so a
# finding resting on a CONTRADICTED + a CORROBORATED verdict floors to
# CONTRADICTED. Lower rank = weaker/more-cautious.
_SUPPORT_RANK: dict[SupportLevel, int] = {
    SupportLevel.CONTRADICTED: 0,
    SupportLevel.SINGLE_SOURCE: 1,
    SupportLevel.CORROBORATED: 2,
}


SYSTEM_PROMPT = (
    "You are a research synthesis analyst. You are given a research goal, its "
    "prioritized sub-questions (numbered S0, S1, …), and a set of cross-checked "
    "verdicts (numbered V0, V1, …), each with its support level and confidence. "
    "Synthesize the verdicts into findings that answer the sub-questions: each "
    "finding is a clear statement, the sub-questions it addresses (by their "
    "S-number), and the verdicts that support it (by their V-number). Compose "
    "across verdicts where they speak to the same point; ground every finding in "
    "at least one verdict. Reflect disputed or single-source support honestly in "
    "the wording — do not present a contradicted verdict as settled. Use only the "
    "provided verdicts; reference items only by their given numbers."
)


class _FindingDraft(BaseModel):
    """Model-output shape for one finding (local indices only — no ids)."""

    statement: str
    detail: str | None = None
    sub_questions: list[int] = Field(default_factory=list)  # S# → plan.sub_questions
    supporting_verdicts: list[int] = Field(default_factory=list)  # V# → verdicts


class _SynthesisOutput(BaseModel):
    """Structured output of the single synthesis call.

    The model authors prose + local indices; all ids and the grounding summary
    are code-attached/derived.
    """

    findings: list[_FindingDraft] = Field(default_factory=list)


class SynthesisAgent:
    """Synthesizes `Verdict`s into `Finding`s via the ``LONG_CONTEXT``-role model.

    Uses ``LONG_CONTEXT`` (CLAUDE.md §6 "long-context summarization"): synthesis
    reads the whole verdict set at once. The role resolves to the same configured
    model as ``PLANNING`` today, so the choice is a forward-flexible label (ops
    can reroute synthesis to a larger-context model via config, no code change),
    not a behavioural change.
    """

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def synthesize(
        self,
        plan: ResearchPlan,
        verdicts: list[Verdict],
        *,
        prior_critique: Critique | None = None,
    ) -> Synthesis:
        """Synthesize ``verdicts`` into a `Synthesis` of `Finding`s.

        On a revision pass (M10b) ``prior_critique`` carries the Editorial
        Critic's last assessment; its rationale + issue details are injected into
        the prompt so re-synthesis *addresses* the critique rather than re-running
        the same inputs (without this feed-forward the revision loop would be
        theater). ``None`` (the default, and every first pass) reproduces the M9
        behavior exactly — backward-compatible.

        Raises `SynthesisError` if ``verdicts`` is empty (never advance on empty
        — verify already raises on zero verdicts upstream, so this is a defensive
        wiring guard) or if no finding survives (the call failed, the model
        returned none, or every finding was dropped for citing no real verdict).
        """
        if not verdicts:
            raise SynthesisError("synthesis received no verdicts")

        model = self._router.for_role(ModelRole.LONG_CONTEXT)
        synthesized_via = f"synthesis:{model.model}"

        output = await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(plan, verdicts, prior_critique),
            schema=_SynthesisOutput,
        )

        findings: list[Finding] = []
        for draft in output.findings:
            finding = self._build_finding(draft, plan, verdicts, synthesized_via)
            if finding is not None:
                findings.append(finding)

        if not findings:
            raise SynthesisError("synthesis produced no findings from the verdicts")
        return Synthesis(findings=findings)

    def _build_finding(
        self,
        draft: _FindingDraft,
        plan: ResearchPlan,
        verdicts: list[Verdict],
        synthesized_via: str,
    ) -> Finding | None:
        """Resolve a model draft into a `Finding` with code-attached provenance.

        Verdict and sub-question local indices are resolved against their own
        lists (out-of-range dropped + logged). Returns ``None`` for a finding
        with no resolvable supporting verdict — a finding must rest on at least
        one real verdict (the M8 drop-empty guard, one layer up). The grounding
        summary is computed *after* the drop check, so it always floors over a
        non-empty set.
        """
        supporting = self._resolve(draft.supporting_verdicts, verdicts)
        if not supporting:
            logger.warning("synthesis: dropping finding with no valid supporting verdict")
            return None
        sub_questions = self._resolve(draft.sub_questions, plan.sub_questions)

        return Finding(
            statement=draft.statement,
            detail=draft.detail,
            sub_question_ids=[sq.id for sq in sub_questions],
            supporting_verdict_ids=[v.id for v in supporting],
            disputed=any(v.support_level is SupportLevel.CONTRADICTED for v in supporting),
            weakest_support=min(
                supporting, key=lambda v: _SUPPORT_RANK[v.support_level]
            ).support_level,
            synthesized_via=synthesized_via,
        )

    @staticmethod
    def _resolve(local_indices: list[int], items: list[_T]) -> list[_T]:
        """Map local indices to items in ``items``, dropping out-of-range ones.

        Generic over `Verdict`/`SubQuestion`; each index space is resolved only
        against its own list, so the two can never cross-resolve. De-dups if the
        model lists an index twice.
        """
        resolved: list[_T] = []
        seen: set[int] = set()
        for i in local_indices:
            if 0 <= i < len(items):
                if i not in seen:
                    resolved.append(items[i])
                    seen.add(i)
            else:
                logger.warning("synthesis: dropping out-of-range local index %s", i)
        return resolved

    @staticmethod
    def _build_prompt(
        plan: ResearchPlan, verdicts: list[Verdict], prior_critique: Critique | None = None
    ) -> str:
        goal = plan.goal or "(no refined goal provided)"
        sub_qs = "\n".join(f"[S{i}] {sq.text}" for i, sq in enumerate(plan.sub_questions))
        verdict_lines = "\n".join(
            f"[V{i}] ({v.support_level.value}, confidence {v.confidence:.2f}) {v.claim}"
            for i, v in enumerate(verdicts)
        )
        critique_block = ""
        if prior_critique is not None:
            issue_lines = "\n".join(
                f"- {issue.kind.value}: {issue.detail}" for issue in prior_critique.issues
            )
            critique_block = (
                "\n\nA prior synthesis was reviewed and needs revision. Address this "
                f"editorial critique:\n{prior_critique.rationale}\n{issue_lines}\n"
            )
        return (
            f"Research goal:\n{goal}\n\n"
            f"Sub-questions:\n{sub_qs or '(none)'}\n\n"
            f"Cross-checked verdicts:\n{verdict_lines}"
            f"{critique_block}\n\n"
            "Synthesize findings that answer the sub-questions, grounded in the verdicts."
        )
