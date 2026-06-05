"""Tests for the Cross-Verification agent (M8).

Hermetic: a `FakeProvider` scripts one `_VerificationOutput` per claim cluster.
The tests pin the contract: evidence ids are code-attached from the real
`Evidence` (never the model), the model references claims only by local index,
out-of-range indices are dropped, ``CORROBORATED`` is code-gated on >=2 distinct
sources, ``CONTRADICTED`` is preserved, per-cluster failures are tolerated, and
empty input / zero output raise.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.agents.cross_verification import (
    CrossVerificationAgent,
    VerificationError,
    _VerdictDraft,
    _VerificationOutput,
)
from app.schemas.research_state import Evidence, SupportLevel
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _ev(claim: str, source_id: str) -> Evidence:
    return Evidence(
        claim=claim,
        source_id=source_id,
        source_url=f"https://{source_id}.com",
        chunk_id="chk_1",
        chunk_text="…",
        confidence=0.8,
        extracted_via="extraction:fake",
    )


def _agent(outputs: list[BaseModel]) -> tuple[CrossVerificationAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return CrossVerificationAgent(router), fake


def _draft(
    level: SupportLevel, *, supporting: list[int], contradicting: list[int] | None = None
) -> _VerdictDraft:
    return _VerdictDraft(
        claim="canonical claim",
        support_level=level,
        confidence=0.7,
        supporting=supporting,
        contradicting=contradicting or [],
    )


# Two claims that share salient tokens, so the blocker groups them into one
# cluster → exactly one model call.
_PAIR = ("solar energy output rises", "solar energy output climbs")


def test_builds_verdict_with_code_attached_ids() -> None:
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.CORROBORATED, supporting=[0, 1])])
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert len(verdicts) == 1
    vd = verdicts[0]
    # ids come from the real Evidence, not the model:
    assert vd.supporting_evidence_ids == [evidence[0].id, evidence[1].id]
    assert vd.verified_via == "verification:planning-model"
    assert vd.id.startswith("vd_")
    assert vd.claim == "canonical claim"


def test_uses_planning_role_and_claims_in_prompt() -> None:
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.CORROBORATED, supporting=[0, 1])])
    agent, fake = _agent([out])
    asyncio.run(agent.verify(evidence))
    assert fake.calls[0].model == "planning-model"
    assert _PAIR[0] in fake.calls[0].prompt


def test_corroborated_requires_two_distinct_sources() -> None:
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.CORROBORATED, supporting=[0, 1])])
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert verdicts[0].support_level is SupportLevel.CORROBORATED


def test_intra_source_block_is_not_corroborated() -> None:
    # Both supporting items are from the SAME source → code downgrades the
    # model's optimistic CORROBORATED to SINGLE_SOURCE.
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_a")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.CORROBORATED, supporting=[0, 1])])
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert verdicts[0].support_level is SupportLevel.SINGLE_SOURCE


def test_contradicted_is_preserved() -> None:
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(
        verdicts=[_draft(SupportLevel.CONTRADICTED, supporting=[0], contradicting=[1])]
    )
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert verdicts[0].support_level is SupportLevel.CONTRADICTED
    assert verdicts[0].contradicting_evidence_ids == [evidence[1].id]


def test_contradicted_with_no_valid_contradicting_is_downgraded() -> None:
    # The model labels it CONTRADICTED but its only contradicting index is
    # out-of-range → resolves to empty → "conflict" with nothing conflicting is
    # downgraded (here to SINGLE_SOURCE: one valid supporting source remains).
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(
        verdicts=[_draft(SupportLevel.CONTRADICTED, supporting=[0], contradicting=[9])]
    )
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert verdicts[0].support_level is SupportLevel.SINGLE_SOURCE
    assert verdicts[0].contradicting_evidence_ids == []


def test_out_of_range_index_is_dropped() -> None:
    # The model cites a local index (5) that was never in the cluster; it is
    # dropped, the verdict survives on the valid index, and nothing raises.
    evidence = [_ev(_PAIR[0], "src_a"), _ev(_PAIR[1], "src_b")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.CORROBORATED, supporting=[0, 5])])
    agent, _ = _agent([out])
    verdicts = asyncio.run(agent.verify(evidence))
    assert verdicts[0].supporting_evidence_ids == [evidence[0].id]
    # only one valid distinct source remains → downgraded
    assert verdicts[0].support_level is SupportLevel.SINGLE_SOURCE


def test_verdict_with_no_valid_supporting_is_dropped_then_raises() -> None:
    evidence = [_ev("a unique standalone claim", "src_a")]
    out = _VerificationOutput(verdicts=[_draft(SupportLevel.SINGLE_SOURCE, supporting=[9])])
    agent, _ = _agent([out])
    with pytest.raises(VerificationError):
        asyncio.run(agent.verify(evidence))


class _Boom(BaseModel):
    """A wrong-typed scripted response — FakeProvider rejects it, simulating a
    failed model call so the agent's per-cluster tolerance can be exercised."""


def test_per_cluster_failure_is_tolerated() -> None:
    # Two unrelated claims → two singleton clusters → two model calls. The first
    # call "fails" (type mismatch), the second succeeds; one verdict survives.
    evidence = [
        _ev("quantum entanglement links particles", "src_a"),
        _ev("volcanoes erupt lava", "src_b"),
    ]
    good = _VerificationOutput(verdicts=[_draft(SupportLevel.SINGLE_SOURCE, supporting=[0])])
    agent, _ = _agent([_Boom(), good])
    verdicts = asyncio.run(agent.verify(evidence))
    assert len(verdicts) == 1


def test_empty_input_raises() -> None:
    agent, _ = _agent([])
    with pytest.raises(VerificationError):
        asyncio.run(agent.verify([]))


def test_zero_verdicts_raises() -> None:
    evidence = [_ev("a standalone claim", "src_a")]
    agent, _ = _agent([_VerificationOutput(verdicts=[])])
    with pytest.raises(VerificationError):
        asyncio.run(agent.verify(evidence))
