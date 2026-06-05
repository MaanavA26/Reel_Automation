"""Tests for the Synthesis agent (M9).

Hermetic: a `FakeProvider` scripts one `_SynthesisOutput` (synthesis is a single
call over the reduced verdict set). The tests pin the contract: ids are
code-attached from the real `Verdict`/`SubQuestion` sets (never the model), the
two local-index spaces resolve independently, the grounding summary
(``disputed`` / ``weakest_support``) is code-derived regardless of model output,
out-of-range indices are dropped, and empty input / zero output raise.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.synthesis import (
    SynthesisAgent,
    SynthesisError,
    _FindingDraft,
    _SynthesisOutput,
)
from app.schemas.research_state import ResearchPlan, SubQuestion, SupportLevel, Verdict
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _verdict(claim: str, level: SupportLevel) -> Verdict:
    return Verdict(
        claim=claim,
        support_level=level,
        supporting_evidence_ids=["ev_x"],
        confidence=0.8,
        verified_via="verification:fake",
    )


def _plan(*sub_question_texts: str) -> ResearchPlan:
    return ResearchPlan(
        goal="goal", sub_questions=[SubQuestion(text=t) for t in sub_question_texts]
    )


def _agent(outputs: list[_SynthesisOutput]) -> tuple[SynthesisAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "long-context-model")},
    )
    return SynthesisAgent(router), fake


def _draft(
    *,
    supporting_verdicts: list[int],
    sub_questions: list[int] | None = None,
    statement: str = "synthesized finding",
) -> _FindingDraft:
    return _FindingDraft(
        statement=statement,
        supporting_verdicts=supporting_verdicts,
        sub_questions=sub_questions or [],
    )


def test_builds_finding_with_code_attached_ids() -> None:
    plan = _plan("what is fusion?")
    verdicts = [
        _verdict("fusion needs heat", SupportLevel.CORROBORATED),
        _verdict("fusion needs pressure", SupportLevel.CORROBORATED),
    ]
    out = _SynthesisOutput(findings=[_draft(supporting_verdicts=[0, 1], sub_questions=[0])])
    agent, _ = _agent([out])
    synthesis = asyncio.run(agent.synthesize(plan, verdicts))
    assert len(synthesis.findings) == 1
    f = synthesis.findings[0]
    assert f.supporting_verdict_ids == [verdicts[0].id, verdicts[1].id]
    assert f.sub_question_ids == [plan.sub_questions[0].id]
    assert f.synthesized_via == "synthesis:long-context-model"
    assert f.id.startswith("fnd_")
    assert f.statement == "synthesized finding"


def test_uses_long_context_role_and_verdict_content_in_prompt() -> None:
    plan = _plan("q")
    verdicts = [_verdict("fusion needs heat", SupportLevel.SINGLE_SOURCE)]
    agent, fake = _agent([_SynthesisOutput(findings=[_draft(supporting_verdicts=[0])])])
    asyncio.run(agent.synthesize(plan, verdicts))
    assert fake.calls[0].model == "long-context-model"
    # the model must see the claim AND its support level to write honest prose:
    assert "fusion needs heat" in fake.calls[0].prompt
    assert "single_source" in fake.calls[0].prompt


def test_contradiction_carried_forward() -> None:
    # The keystone guard: the model is given NO grounding field; code derives
    # disputed/weakest_support from the cited verdict's support_level.
    plan = _plan("q")
    verdicts = [_verdict("disputed claim", SupportLevel.CONTRADICTED)]
    agent, _ = _agent([_SynthesisOutput(findings=[_draft(supporting_verdicts=[0])])])
    synthesis = asyncio.run(agent.synthesize(plan, verdicts))
    f = synthesis.findings[0]
    assert f.disputed is True
    assert f.weakest_support is SupportLevel.CONTRADICTED


def test_weakest_support_is_floor_over_verdicts() -> None:
    plan = _plan("q")
    verdicts = [
        _verdict("strong claim", SupportLevel.CORROBORATED),
        _verdict("thin claim", SupportLevel.SINGLE_SOURCE),
    ]
    agent, _ = _agent([_SynthesisOutput(findings=[_draft(supporting_verdicts=[0, 1])])])
    synthesis = asyncio.run(agent.synthesize(plan, verdicts))
    f = synthesis.findings[0]
    assert f.weakest_support is SupportLevel.SINGLE_SOURCE  # most-cautious wins
    assert f.disputed is False  # no CONTRADICTED among cited verdicts


def test_index_spaces_do_not_cross_resolve() -> None:
    # A verdict index and a sub-question index of the same value resolve against
    # their OWN lists — no cross-wiring (the two-index-space hazard).
    plan = _plan("the sub-question")
    verdicts = [_verdict("the verdict", SupportLevel.CORROBORATED)]
    out = _SynthesisOutput(findings=[_draft(supporting_verdicts=[0], sub_questions=[0])])
    agent, _ = _agent([out])
    f = asyncio.run(agent.synthesize(plan, verdicts)).findings[0]
    assert f.supporting_verdict_ids == [verdicts[0].id]
    assert f.sub_question_ids == [plan.sub_questions[0].id]
    assert f.supporting_verdict_ids != f.sub_question_ids


def test_out_of_range_verdict_index_is_dropped() -> None:
    plan = _plan("q")
    verdicts = [_verdict("real claim", SupportLevel.CORROBORATED)]
    out = _SynthesisOutput(findings=[_draft(supporting_verdicts=[0, 9])])
    agent, _ = _agent([out])
    f = asyncio.run(agent.synthesize(plan, verdicts)).findings[0]
    assert f.supporting_verdict_ids == [verdicts[0].id]


def test_finding_with_no_resolvable_verdict_is_dropped_then_raises() -> None:
    plan = _plan("q")
    verdicts = [_verdict("real claim", SupportLevel.CORROBORATED)]
    out = _SynthesisOutput(findings=[_draft(supporting_verdicts=[9])])
    agent, _ = _agent([out])
    with pytest.raises(SynthesisError):
        asyncio.run(agent.synthesize(plan, verdicts))


def test_thin_synthesis_does_not_raise() -> None:
    # All verdicts single-source → a valid, cautious synthesis, not a failure.
    plan = _plan("q")
    verdicts = [_verdict("thin claim", SupportLevel.SINGLE_SOURCE)]
    out = _SynthesisOutput(findings=[_draft(supporting_verdicts=[0])])
    agent, _ = _agent([out])
    synthesis = asyncio.run(agent.synthesize(plan, verdicts))
    assert synthesis.findings[0].weakest_support is SupportLevel.SINGLE_SOURCE


def test_empty_input_raises() -> None:
    agent, _ = _agent([])
    with pytest.raises(SynthesisError):
        asyncio.run(agent.synthesize(_plan("q"), []))


def test_zero_findings_raises() -> None:
    plan = _plan("q")
    verdicts = [_verdict("claim", SupportLevel.CORROBORATED)]
    agent, _ = _agent([_SynthesisOutput(findings=[])])
    with pytest.raises(SynthesisError):
        asyncio.run(agent.synthesize(plan, verdicts))
