"""Tests for the deterministic coverage tool (M10).

Pins the contract the Editorial Critic gates its decision on: a sub-question is
covered iff some finding lists its id, uncovered ids come back in plan (priority)
order, and full coverage yields an empty list.
"""

from __future__ import annotations

from app.schemas.research_state import (
    Finding,
    ResearchPlan,
    SubQuestion,
    SupportLevel,
    Synthesis,
)
from app.services.reasoning.coverage import uncovered_sub_question_ids


def _finding(sub_question_ids: list[str]) -> Finding:
    return Finding(
        statement="f",
        sub_question_ids=sub_question_ids,
        supporting_verdict_ids=["vd_1"],
        disputed=False,
        weakest_support=SupportLevel.CORROBORATED,
        synthesized_via="synthesis:fake",
    )


def test_uncovered_returns_sub_questions_with_no_finding() -> None:
    plan = ResearchPlan(sub_questions=[SubQuestion(text="a"), SubQuestion(text="b")])
    synthesis = Synthesis(findings=[_finding([plan.sub_questions[0].id])])
    assert uncovered_sub_question_ids(plan, synthesis) == [plan.sub_questions[1].id]


def test_full_coverage_returns_empty() -> None:
    plan = ResearchPlan(sub_questions=[SubQuestion(text="a")])
    synthesis = Synthesis(findings=[_finding([plan.sub_questions[0].id])])
    assert uncovered_sub_question_ids(plan, synthesis) == []


def test_no_findings_means_all_uncovered_in_plan_order() -> None:
    plan = ResearchPlan(sub_questions=[SubQuestion(text="a"), SubQuestion(text="b")])
    expected = [sq.id for sq in plan.sub_questions]
    assert uncovered_sub_question_ids(plan, Synthesis()) == expected
