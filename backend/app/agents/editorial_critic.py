"""Editorial Critic agent — assesses a synthesis for coverage and quality.

An *agent* (judgment, CLAUDE.md §4): given the research plan and the `Synthesis`
built from cross-checked verdicts (M9), it judges the *composition* — the
synthesis's quality — and records an accept/revise assessment. It is the final
node of the Knowledge Reasoning band and realizes the quality-gate logic ADR 0005
deferred to M10.

The agent/tool split (CLAUDE.md §4): **coverage** (which sub-questions are
addressed by zero findings) is a deterministic set-difference, owned by the
`coverage` tool — the model never computes it. The agent judges only what code
cannot: redundancy, balance, clarity, and whether a finding's *prose* overstates
past its code-attached ``disputed`` / ``weakest_support`` flags.

The §11 boundary is made structural the same way M8/M9 did, one layer up:

1. The model references findings and sub-questions only by *local index* into the
   numbered lists it was shown (``F#`` / ``S#``, two separate fields resolved
   against separate lists); code resolves them to real ids and drops any out of
   range, and a quality issue resolving to nothing is dropped — the model cannot
   raise an issue about a finding that does not exist.
2. The accept/revise ``decision`` and the coverage gap are **code-derived** — the
   model gets no field to self-report either, so it cannot vote ACCEPT past an
   objective coverage gap, nor hallucinate/suppress a gap.

**Scope (M10a):** this agent produces the assessment; the ``decision`` is
*recorded, not yet routed on* (the critique node routes forward to publish like
every other band). The bounded revision loop that consumes ``decision`` —
back-edge to re-synthesize, iteration counter, feed-forward — is M10b. This
mirrors ADR 0005 shipping the ``error`` field before its consumers existed. A
disputed/single-source finding is **not** a revise trigger: it is a valid,
already-surfaced outcome (ADR 0010/0011), and re-synthesis cannot un-dispute it —
only coverage gaps and quality issues trigger revise. See ADR 0012.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, Field

from app.schemas.research_state import (
    Critique,
    CritiqueDecision,
    QualityIssue,
    QualityIssueKind,
    ResearchPlan,
    Synthesis,
)
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter
from app.services.reasoning.coverage import uncovered_sub_question_ids

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class CriticError(RuntimeError):
    """Raised when the critic is given a synthesis with no findings to assess."""


SYSTEM_PROMPT = (
    "You are an editorial critic for a research engine. You are given a research "
    "goal, its sub-questions (numbered S0, S1, …), and the synthesized findings "
    "(numbered F0, F1, …), each with its support strength (whether it is disputed "
    "or single-source). Assess the quality of the synthesis and raise concrete "
    "issues: redundant findings restating one point, a sub-question answered "
    "one-sidedly (imbalanced), a finding whose wording overstates past its "
    "disputed/single-source support (overstated), or a vague/unclear statement. "
    "For each issue give its kind, a short detail, and the findings (by F-number) "
    "and/or sub-questions (by S-number) it concerns. Also give a one-paragraph "
    "rationale. Do not assess coverage (which sub-questions are unanswered) — the "
    "engine computes that. Reference items only by their given numbers; raise an "
    "issue only about findings/sub-questions that exist. If the synthesis is "
    "sound, return no issues."
)


class _IssueDraft(BaseModel):
    """Model-output shape for one quality issue (local indices only — no ids)."""

    kind: QualityIssueKind
    detail: str
    findings: list[int] = Field(default_factory=list)  # F# → synthesis.findings
    sub_questions: list[int] = Field(default_factory=list)  # S# → plan.sub_questions


class _CritiqueOutput(BaseModel):
    """Structured output of the single critique call.

    The model authors quality issues (prose + local indices) and a rationale; the
    coverage gap and the accept/revise decision are code-derived, not here.
    """

    issues: list[_IssueDraft] = Field(default_factory=list)
    rationale: str = ""


class EditorialCriticAgent:
    """Assesses a `Synthesis` into a `Critique` via the ``PLANNING``-role model.

    Reuses ``PLANNING`` (adversarial analytical evaluation, like cross-verification
    — not summarization): a critique-specific role is added only when policy routes
    it to a distinct model (ADR 0003), which it does not yet.
    """

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def critique(self, plan: ResearchPlan, synthesis: Synthesis) -> Critique:
        """Assess ``synthesis`` and return a `Critique`.

        Coverage and the accept/revise decision are code-derived; the model
        authors only the quality issues + rationale. Raises `CriticError` if the
        synthesis has no findings (never assess on empty — synthesize already
        raises on zero findings upstream, so this is a defensive wiring guard).
        "Found nothing wrong" — zero issues and full coverage — is a valid ACCEPT,
        not a failure (the inverse of the synthesis empty-is-failure contract).
        """
        if not synthesis.findings:
            raise CriticError("editorial critic received a synthesis with no findings")

        model = self._router.for_role(ModelRole.PLANNING)
        critiqued_via = f"critique:{model.model}"

        output = await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(plan, synthesis),
            schema=_CritiqueOutput,
        )

        issues = [
            issue
            for draft in output.issues
            if (issue := self._build_issue(draft, plan, synthesis)) is not None
        ]
        uncovered = uncovered_sub_question_ids(plan, synthesis)
        decision = CritiqueDecision.REVISE if (uncovered or issues) else CritiqueDecision.ACCEPT
        return Critique(
            decision=decision,
            uncovered_sub_question_ids=uncovered,
            issues=issues,
            rationale=output.rationale,
            critiqued_via=critiqued_via,
        )

    def _build_issue(
        self, draft: _IssueDraft, plan: ResearchPlan, synthesis: Synthesis
    ) -> QualityIssue | None:
        """Resolve a model issue draft to code-attached ids; drop if it is about nothing.

        Finding and sub-question local indices are resolved against their own
        lists (out-of-range dropped). Returns ``None`` if the issue resolves to no
        real finding *and* no real sub-question — it cannot be about anything that
        exists (the M9 drop-empty guard, one layer up).
        """
        findings = self._resolve(draft.findings, synthesis.findings)
        sub_questions = self._resolve(draft.sub_questions, plan.sub_questions)
        if not findings and not sub_questions:
            logger.warning("editorial critic: dropping issue that references nothing real")
            return None
        return QualityIssue(
            kind=draft.kind,
            detail=draft.detail,
            finding_ids=[f.id for f in findings],
            sub_question_ids=[sq.id for sq in sub_questions],
        )

    @staticmethod
    def _resolve(local_indices: list[int], items: list[_T]) -> list[_T]:
        """Map local indices to items, dropping out-of-range ones (de-duped)."""
        resolved: list[_T] = []
        seen: set[int] = set()
        for i in local_indices:
            if 0 <= i < len(items):
                if i not in seen:
                    resolved.append(items[i])
                    seen.add(i)
            else:
                logger.warning("editorial critic: dropping out-of-range local index %s", i)
        return resolved

    @staticmethod
    def _build_prompt(plan: ResearchPlan, synthesis: Synthesis) -> str:
        goal = plan.goal or "(no refined goal provided)"
        sub_qs = "\n".join(f"[S{i}] {sq.text}" for i, sq in enumerate(plan.sub_questions))
        finding_lines = "\n".join(
            f"[F{i}] ({'disputed' if f.disputed else f.weakest_support.value}) {f.statement}"
            for i, f in enumerate(synthesis.findings)
        )
        return (
            f"Research goal:\n{goal}\n\n"
            f"Sub-questions:\n{sub_qs or '(none)'}\n\n"
            f"Synthesized findings:\n{finding_lines}\n\n"
            "Assess the synthesis quality and raise any issues."
        )
