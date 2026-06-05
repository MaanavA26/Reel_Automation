"""Tests for the Creator Packet agent (M12, the Short-Form Content Strategist).

Hermetic: a `FakeProvider` scripts one `_PacketOutput`. The tests pin the
contract one layer past M11: hook/angle/narrative ids are code-attached from the
real findings (the model authors no ids), the `LONG_CONTEXT` role + the report
title/findings reach the prompt, fabricated indices are dropped, key facts +
warnings are code-derived over the FULL findings set (so an uncited disputed
finding STILL produces a non-omittable warning — the keystone), and empty/zero
raise while a thin/all-disputed packet ships.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.creator_packet import (
    CreatorPacketAgent,
    CreatorPacketError,
    _AngleDraft,
    _HookDraft,
    _NarrativeDraft,
    _PacketOutput,
)
from app.schemas.research_state import (
    CaveatKind,
    Finding,
    Report,
    ReportSection,
    SupportLevel,
)
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _agent(outputs: list[_PacketOutput]) -> tuple[CreatorPacketAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "long-context-model")},
    )
    return CreatorPacketAgent(router), fake


def _finding(
    statement: str, *, disputed: bool = False, support: SupportLevel | None = None
) -> Finding:
    return Finding(
        statement=statement,
        supporting_verdict_ids=["vd_1"],
        disputed=disputed,
        weakest_support=support or SupportLevel.CORROBORATED,
        synthesized_via="synthesis:fake",
    )


def _report() -> Report:
    return Report(
        title="Solar Surge",
        abstract="Abstract",
        sections=[ReportSection(heading="Overview", narrative="prose", finding_ids=["fnd_x"])],
        published_via="report:long-context-model",
    )


def _out(
    hooks: list[_HookDraft] | None = None,
    angles: list[_AngleDraft] | None = None,
    narratives: list[_NarrativeDraft] | None = None,
) -> _PacketOutput:
    return _PacketOutput(
        hooks=hooks or [],
        angles=angles or [],
        narratives=narratives or [],
    )


def test_builds_packet_with_code_attached_finding_ids() -> None:
    findings = [_finding("solar capacity grew sharply")]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="Did you know?", findings=[0])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert packet.id.startswith("pkt_")
    assert packet.published_via == "packet:long-context-model"
    assert packet.report_id.startswith("rpt_")
    assert packet.hooks[0].finding_ids == [findings[0].id]


def test_uses_long_context_role_and_report_in_prompt() -> None:
    findings = [_finding("solar capacity grew sharply")]
    agent, fake = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    asyncio.run(agent.generate(_report(), findings))
    assert fake.calls[0].model == "long-context-model"
    assert "Solar Surge" in fake.calls[0].prompt
    assert "solar capacity grew sharply" in fake.calls[0].prompt


def test_key_facts_are_code_derived_from_all_findings() -> None:
    # The model output references only F0, but key_facts project EVERY finding.
    findings = [_finding("fact A"), _finding("fact B")]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert [kf.statement for kf in packet.key_facts] == ["fact A", "fact B"]
    assert [kf.finding_id for kf in packet.key_facts] == [findings[0].id, findings[1].id]


def test_fabricated_finding_index_is_dropped() -> None:
    findings = [_finding("real")]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0, 9])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert packet.hooks[0].finding_ids == [findings[0].id]


def test_element_with_only_fabricated_findings_dropped() -> None:
    findings = [_finding("real")]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[9])])])
    with pytest.raises(CreatorPacketError):
        asyncio.run(agent.generate(_report(), findings))


def test_angles_and_narratives_resolve_ids() -> None:
    findings = [_finding("real")]
    agent, _ = _agent(
        [
            _out(
                angles=[_AngleDraft(angle="counterintuitive", rationale="why", findings=[0])],
                narratives=[
                    _NarrativeDraft(title="arc", script_outline="beat 1; beat 2", findings=[0])
                ],
            )
        ]
    )
    packet = asyncio.run(agent.generate(_report(), findings))
    assert packet.angles[0].finding_ids == [findings[0].id]
    assert packet.narratives[0].finding_ids == [findings[0].id]


def test_warnings_cover_uncited_disputed_finding() -> None:
    # THE KEYSTONE: a disputed finding NO hook/angle/narrative references still
    # produces a non-omittable warning — warnings range over the full findings
    # set, not the cited subset, and tie back by shared finding_ids.
    clean = _finding("safe claim")
    disputed = _finding("contested claim", disputed=True, support=SupportLevel.CONTRADICTED)
    findings = [clean, disputed]  # index 1 disputed, NOT cited below
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert any(
        w.kind is CaveatKind.DISPUTED_FINDING and disputed.id in w.finding_ids
        for w in packet.warnings
    )


def test_single_source_finding_warns() -> None:
    findings = [_finding("thin", support=SupportLevel.SINGLE_SOURCE)]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert any(w.kind is CaveatKind.WEAK_SUPPORT for w in packet.warnings)


def test_all_clean_findings_no_warnings() -> None:
    findings = [_finding("solid")]
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    packet = asyncio.run(agent.generate(_report(), findings))
    assert packet.warnings == []


def test_empty_findings_raises() -> None:
    agent, _ = _agent([_out(hooks=[_HookDraft(text="hook", findings=[0])])])
    with pytest.raises(CreatorPacketError):
        asyncio.run(agent.generate(_report(), []))


def test_zero_resolvable_elements_raises() -> None:
    # The model returns no elements at all → nothing survives → raise.
    findings = [_finding("real")]
    agent, _ = _agent([_out()])
    with pytest.raises(CreatorPacketError):
        asyncio.run(agent.generate(_report(), findings))
