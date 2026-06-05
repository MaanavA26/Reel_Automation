"""Tests for the Editorial Critic agent (M10a).

Hermetic: a `FakeProvider` scripts one `_CritiqueOutput`. The tests pin the
contract: issue ids are code-attached from the real `Finding`/`SubQuestion` sets,
the accept/revise decision is code-derived (a coverage gap forces REVISE past a
model that found nothing wrong; a disputed finding alone does NOT), issues about
nothing real are dropped, and an empty synthesis raises.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.editorial_critic import (
    CriticError,
    EditorialCriticAgent,
    _CritiqueOutput,
    _IssueDraft,
)
from app.schemas.research_state import (
    CritiqueDecision,
    Finding,
    QualityIssueKind,
    ResearchPlan,
    SubQuestion,
    SupportLevel,
    Synthesis,
)
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _agent(outputs: list[_CritiqueOutput]) -> tuple[EditorialCriticAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return EditorialCriticAgent(router), fake


def _plan(*texts: str) -> ResearchPlan:
    return ResearchPlan(goal="goal", sub_questions=[SubQuestion(text=t) for t in texts])


def _finding(
    statement: str,
    sub_question_ids: list[str],
    *,
    disputed: bool = False,
    weakest: SupportLevel = SupportLevel.CORROBORATED,
) -> Finding:
    return Finding(
        statement=statement,
        sub_question_ids=sub_question_ids,
        supporting_verdict_ids=["vd_1"],
        disputed=disputed,
        weakest_support=weakest,
        synthesized_via="synthesis:fake",
    )


def _issue(kind: QualityIssueKind, *, findings: list[int], detail: str = "x") -> _IssueDraft:
    return _IssueDraft(kind=kind, detail=detail, findings=findings)


def _covered_synthesis(plan: ResearchPlan) -> Synthesis:
    """A synthesis whose single finding covers every sub-question in ``plan``."""
    return Synthesis(findings=[_finding("f", [sq.id for sq in plan.sub_questions])])


def test_builds_critique_with_code_attached_issue_ids() -> None:
    plan = _plan("q")
    synthesis = _covered_synthesis(plan)
    out = _CritiqueOutput(issues=[_issue(QualityIssueKind.UNCLEAR, findings=[0])], rationale="r")
    agent, _ = _agent([out])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.id.startswith("crit_")
    assert critique.critiqued_via == "critique:planning-model"
    assert critique.rationale == "r"
    assert critique.issues[0].finding_ids == [synthesis.findings[0].id]


def test_uses_planning_role_and_finding_content_in_prompt() -> None:
    plan = _plan("q")
    synthesis = Synthesis(findings=[_finding("a notable finding", [plan.sub_questions[0].id])])
    agent, fake = _agent([_CritiqueOutput(rationale="r")])
    asyncio.run(agent.critique(plan, synthesis))
    assert fake.calls[0].model == "planning-model"
    assert "a notable finding" in fake.calls[0].prompt


def test_uncovered_forces_revise_despite_no_issues() -> None:
    # The keystone: the model finds nothing wrong (no issues), but a sub-question
    # is uncovered → code derives REVISE; the model can't vote ACCEPT past a gap.
    plan = _plan("covered", "uncovered")
    synthesis = Synthesis(findings=[_finding("f", [plan.sub_questions[0].id])])
    agent, _ = _agent([_CritiqueOutput(issues=[], rationale="looks fine")])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.decision is CritiqueDecision.REVISE
    assert critique.uncovered_sub_question_ids == [plan.sub_questions[1].id]


def test_quality_issue_triggers_revise() -> None:
    plan = _plan("q")
    synthesis = _covered_synthesis(plan)
    out = _CritiqueOutput(issues=[_issue(QualityIssueKind.REDUNDANT, findings=[0])], rationale="r")
    agent, _ = _agent([out])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.decision is CritiqueDecision.REVISE


def test_clean_synthesis_is_accept() -> None:
    # Full coverage + zero issues → ACCEPT, no raise ("found nothing wrong" is
    # success here, the inverse of synthesis's empty-is-failure).
    plan = _plan("q")
    synthesis = _covered_synthesis(plan)
    agent, _ = _agent([_CritiqueOutput(issues=[], rationale="sound")])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.decision is CritiqueDecision.ACCEPT
    assert critique.issues == []


def test_disputed_finding_alone_is_not_revise() -> None:
    # A disputed finding is a valid surfaced outcome, not a revise trigger
    # (ADR 0010/0011): full coverage + no issues → ACCEPT even when disputed.
    plan = _plan("q")
    synthesis = Synthesis(
        findings=[
            _finding(
                "f", [plan.sub_questions[0].id], disputed=True, weakest=SupportLevel.CONTRADICTED
            )
        ]
    )
    agent, _ = _agent([_CritiqueOutput(issues=[], rationale="disputed but honest")])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.decision is CritiqueDecision.ACCEPT


def test_issue_about_nothing_real_is_dropped() -> None:
    # The model raises an issue citing only out-of-range indices → it resolves to
    # nothing and is dropped; with full coverage the result is ACCEPT.
    plan = _plan("q")
    synthesis = _covered_synthesis(plan)
    out = _CritiqueOutput(
        issues=[
            _IssueDraft(kind=QualityIssueKind.UNCLEAR, detail="x", findings=[9], sub_questions=[9])
        ],
        rationale="r",
    )
    agent, _ = _agent([out])
    critique = asyncio.run(agent.critique(plan, synthesis))
    assert critique.issues == []
    assert critique.decision is CritiqueDecision.ACCEPT


def test_empty_synthesis_raises() -> None:
    agent, _ = _agent([_CritiqueOutput(rationale="r")])
    with pytest.raises(CriticError):
        asyncio.run(agent.critique(_plan("q"), Synthesis()))
