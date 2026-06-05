"""Deterministic coverage analysis — which sub-questions the synthesis missed.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): given the
research plan and the synthesis, it computes which `SubQuestion`s are addressed by
zero `Finding`s. This is the deterministic *floor* of the Editorial Critic (M10):
coverage is a code-computable set-difference over the `Finding.sub_question_ids`
linkage M9 records, so it is owned by code, never the model — the critic agent
judges only what code cannot (quality), and code's coverage verdict is what the
accept/revise decision is gated on (ADR 0011 said M10 *reads* the linkage; this is
that read). See ADR 0012.
"""

from __future__ import annotations

from app.schemas.research_state import ResearchPlan, Synthesis


def uncovered_sub_question_ids(plan: ResearchPlan, synthesis: Synthesis) -> list[str]:
    """Return the ids of sub-questions addressed by zero findings, in plan order.

    A `SubQuestion` is *covered* iff at least one `Finding` lists its id in
    ``sub_question_ids``. The result preserves ``plan.sub_questions`` order
    (head = highest priority), so the gap list is itself priority-ranked.
    Deterministic: identical ``(plan, synthesis)`` yields an identical list.
    """
    covered: set[str] = set()
    for finding in synthesis.findings:
        covered.update(finding.sub_question_ids)
    return [sq.id for sq in plan.sub_questions if sq.id not in covered]
