"""Property-based tests for the Deep Research state/provenance schema (ADR 0001).

These complement the example-based tests in `test_research_state.py` by asserting
the schema's *invariants* hold across many generated inputs rather than a handful
of hand-written cases:

- **Round-trip:** ``model_validate(model_dump()) == m`` for every model.
- **ID prefix + uniqueness:** ``_gen_id`` always yields the right prefix and never
  collides across many draws.
- **Strictness:** ``extra='forbid'`` rejects any unknown key.
- **Bounded confidence:** ``Evidence.confidence`` / ``Verdict.confidence`` reject
  values outside ``[0.0, 1.0]``.
- **Timezone-aware timestamps:** default-constructed datetime fields are tz-aware.

`hypothesis` is a dev/test dependency that the offline build sandbox cannot install
(no PyPI). The module ``importorskip``s it so the suite stays green offline; when
the dependency is present (CI, local dev with extras), the properties run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

pytest.importorskip("hypothesis")

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Chunk,
    Citation,
    ContentAngle,
    CreatorPacket,
    CreatorWarning,
    Critique,
    CritiqueDecision,
    Evidence,
    Finding,
    HookIdea,
    JobStatus,
    KeyFact,
    KnowledgeAcquisitionState,
    KnowledgeReasoningState,
    NarrativeOption,
    QualityIssue,
    QualityIssueKind,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchPublishingState,
    ResearchState,
    Source,
    SourceType,
    SubQuestion,
    SupportLevel,
    Synthesis,
    Verdict,
    _gen_id,
)

# ---------------------------------------------------------------------------
# Shared building-block strategies (bounded + deterministic).
# ---------------------------------------------------------------------------

# Bounded text: keep examples small and printable so failures are readable and
# the suite stays fast.
_TEXT = st.text(max_size=24)
_OPTIONAL_TEXT = st.none() | _TEXT

# Pin every generated datetime to a fixed UTC window. Round-trip equality is
# exact in dict mode, so the only requirement is that the value survives a
# dump/validate cycle unchanged; pinning tz to UTC avoids tz-offset ambiguity.
_DATETIMES = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)

# In-range confidence: the schema constrains these to [0.0, 1.0].
_CONFIDENCE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Out-of-range confidence (real, finite, strictly outside [0, 1]).
_BAD_CONFIDENCE = st.floats(allow_nan=False, allow_infinity=False).filter(
    lambda x: x < 0.0 or x > 1.0
)

# Short lists of opaque ids; the schema does not validate referential integrity
# (that is the agents' job), so arbitrary short strings suffice here.
_ID_LIST = st.lists(_TEXT, max_size=4)

_SOURCE_TYPES = st.sampled_from(list(SourceType))
_SUPPORT_LEVELS = st.sampled_from(list(SupportLevel))


@st.composite
def sources(draw: st.DrawFn) -> Source:
    return Source(
        url=draw(_TEXT),
        type=draw(_SOURCE_TYPES),
        discovered_via=draw(_TEXT),
        title=draw(_OPTIONAL_TEXT),
        discovered_at=draw(_DATETIMES),
        raw_metadata=draw(st.dictionaries(_TEXT, _TEXT, max_size=3)),
    )


@st.composite
def chunks(draw: st.DrawFn) -> Chunk:
    return Chunk(
        source_id=draw(_TEXT),
        text=draw(_TEXT),
        position=draw(st.none() | st.integers(min_value=0, max_value=1000)),
    )


@st.composite
def evidences(draw: st.DrawFn) -> Evidence:
    return Evidence(
        claim=draw(_TEXT),
        source_id=draw(_TEXT),
        source_url=draw(_TEXT),
        chunk_id=draw(_TEXT),
        chunk_text=draw(_TEXT),
        confidence=draw(_CONFIDENCE),
        extracted_at=draw(_DATETIMES),
        extracted_via=draw(_TEXT),
    )


@st.composite
def verdicts(draw: st.DrawFn) -> Verdict:
    return Verdict(
        claim=draw(_TEXT),
        support_level=draw(_SUPPORT_LEVELS),
        supporting_evidence_ids=draw(_ID_LIST),
        contradicting_evidence_ids=draw(_ID_LIST),
        confidence=draw(_CONFIDENCE),
        verified_at=draw(_DATETIMES),
        verified_via=draw(_TEXT),
    )


@st.composite
def findings(draw: st.DrawFn) -> Finding:
    return Finding(
        statement=draw(_TEXT),
        detail=draw(_OPTIONAL_TEXT),
        sub_question_ids=draw(_ID_LIST),
        supporting_verdict_ids=draw(_ID_LIST),
        disputed=draw(st.booleans()),
        weakest_support=draw(_SUPPORT_LEVELS),
        synthesized_at=draw(_DATETIMES),
        synthesized_via=draw(_TEXT),
    )


@st.composite
def quality_issues(draw: st.DrawFn) -> QualityIssue:
    return QualityIssue(
        kind=draw(st.sampled_from(list(QualityIssueKind))),
        detail=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
        sub_question_ids=draw(_ID_LIST),
    )


@st.composite
def critiques(draw: st.DrawFn) -> Critique:
    return Critique(
        decision=draw(st.sampled_from(list(CritiqueDecision))),
        uncovered_sub_question_ids=draw(_ID_LIST),
        issues=draw(st.lists(quality_issues(), max_size=3)),
        rationale=draw(_TEXT),
        critiqued_at=draw(_DATETIMES),
        critiqued_via=draw(_TEXT),
    )


@st.composite
def synthesate(draw: st.DrawFn) -> Synthesis:
    return Synthesis(findings=draw(st.lists(findings(), max_size=3)))


@st.composite
def caveats(draw: st.DrawFn) -> Caveat:
    return Caveat(
        kind=draw(st.sampled_from(list(CaveatKind))),
        detail=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
        sub_question_ids=draw(_ID_LIST),
        critique_id=draw(_OPTIONAL_TEXT),
    )


@st.composite
def citations(draw: st.DrawFn) -> Citation:
    return Citation(
        source_id=draw(_TEXT),
        source_url=draw(_TEXT),
        source_type=draw(_SOURCE_TYPES),
        title=draw(_OPTIONAL_TEXT),
        evidence_ids=draw(_ID_LIST),
        verdict_ids=draw(_ID_LIST),
    )


@st.composite
def report_sections(draw: st.DrawFn) -> ReportSection:
    return ReportSection(
        heading=draw(_TEXT),
        narrative=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
        sub_question_ids=draw(_ID_LIST),
    )


@st.composite
def reports(draw: st.DrawFn) -> Report:
    return Report(
        title=draw(_TEXT),
        abstract=draw(_TEXT),
        sections=draw(st.lists(report_sections(), max_size=3)),
        citations=draw(st.lists(citations(), max_size=3)),
        caveats=draw(st.lists(caveats(), max_size=3)),
        published_at=draw(_DATETIMES),
        published_via=draw(_TEXT),
    )


@st.composite
def creator_warnings(draw: st.DrawFn) -> CreatorWarning:
    return CreatorWarning(
        kind=draw(st.sampled_from([CaveatKind.DISPUTED_FINDING, CaveatKind.WEAK_SUPPORT])),
        detail=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
    )


@st.composite
def hook_ideas(draw: st.DrawFn) -> HookIdea:
    return HookIdea(text=draw(_TEXT), finding_ids=draw(_ID_LIST))


@st.composite
def content_angles(draw: st.DrawFn) -> ContentAngle:
    return ContentAngle(
        angle=draw(_TEXT),
        rationale=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
    )


@st.composite
def narrative_options(draw: st.DrawFn) -> NarrativeOption:
    return NarrativeOption(
        title=draw(_TEXT),
        script_outline=draw(_TEXT),
        finding_ids=draw(_ID_LIST),
    )


@st.composite
def key_facts(draw: st.DrawFn) -> KeyFact:
    return KeyFact(
        statement=draw(_TEXT),
        finding_id=draw(_TEXT),
        disputed=draw(st.booleans()),
        weakest_support=draw(_SUPPORT_LEVELS),
    )


@st.composite
def creator_packets(draw: st.DrawFn) -> CreatorPacket:
    return CreatorPacket(
        report_id=draw(_TEXT),
        hooks=draw(st.lists(hook_ideas(), max_size=3)),
        angles=draw(st.lists(content_angles(), max_size=3)),
        narratives=draw(st.lists(narrative_options(), max_size=3)),
        key_facts=draw(st.lists(key_facts(), max_size=3)),
        warnings=draw(st.lists(creator_warnings(), max_size=3)),
        created_at=draw(_DATETIMES),
        published_via=draw(_TEXT),
    )


@st.composite
def sub_questions(draw: st.DrawFn) -> SubQuestion:
    return SubQuestion(text=draw(_TEXT), rationale=draw(_OPTIONAL_TEXT))


@st.composite
def research_plans(draw: st.DrawFn) -> ResearchPlan:
    return ResearchPlan(
        goal=draw(_OPTIONAL_TEXT),
        sub_questions=draw(st.lists(sub_questions(), max_size=3)),
        created_at=draw(_DATETIMES),
    )


@st.composite
def acquisition_states(draw: st.DrawFn) -> KnowledgeAcquisitionState:
    return KnowledgeAcquisitionState(
        sources=draw(st.lists(sources(), max_size=3)),
        chunks=draw(st.lists(chunks(), max_size=3)),
        evidence=draw(st.lists(evidences(), max_size=3)),
    )


@st.composite
def reasoning_states(draw: st.DrawFn) -> KnowledgeReasoningState:
    return KnowledgeReasoningState(
        verdicts=draw(st.lists(verdicts(), max_size=3)),
        synthesis=draw(synthesate()),
        critiques=draw(st.lists(critiques(), max_size=3)),
    )


@st.composite
def publishing_states(draw: st.DrawFn) -> ResearchPublishingState:
    return ResearchPublishingState(
        reports=draw(st.lists(reports(), max_size=2)),
        packets=draw(st.lists(creator_packets(), max_size=2)),
    )


@st.composite
def research_states(draw: st.DrawFn) -> ResearchState:
    return ResearchState(
        topic=draw(_TEXT),
        status=draw(st.sampled_from(list(JobStatus))),
        created_at=draw(_DATETIMES),
        updated_at=draw(_DATETIMES),
        error=draw(_OPTIONAL_TEXT),
        revision_iteration=draw(st.integers(min_value=0, max_value=10)),
        plan=draw(research_plans()),
        acquisition=draw(acquisition_states()),
        reasoning=draw(reasoning_states()),
        publishing=draw(publishing_states()),
    )


# Registry of every model -> a strategy producing valid instances. Round-trip and
# strictness invariants are parametrized over this single source of truth so a new
# schema model is one registry line away from full property coverage.
MODEL_STRATEGIES: dict[type[BaseModel], st.SearchStrategy[BaseModel]] = {
    Source: sources(),
    Chunk: chunks(),
    Evidence: evidences(),
    KnowledgeAcquisitionState: acquisition_states(),
    Verdict: verdicts(),
    Finding: findings(),
    Synthesis: synthesate(),
    QualityIssue: quality_issues(),
    Critique: critiques(),
    KnowledgeReasoningState: reasoning_states(),
    Caveat: caveats(),
    Citation: citations(),
    ReportSection: report_sections(),
    Report: reports(),
    CreatorWarning: creator_warnings(),
    HookIdea: hook_ideas(),
    ContentAngle: content_angles(),
    NarrativeOption: narrative_options(),
    KeyFact: key_facts(),
    CreatorPacket: creator_packets(),
    SubQuestion: sub_questions(),
    ResearchPlan: research_plans(),
    ResearchPublishingState: publishing_states(),
    ResearchState: research_states(),
}

# Models whose `id` field is minted by `_gen_id`, mapped to the expected prefix.
ID_PREFIXES: dict[type[BaseModel], str] = {
    Source: "src",
    Chunk: "chk",
    Evidence: "ev",
    Verdict: "vd",
    Finding: "fnd",
    Critique: "crit",
    Citation: "cit",
    ReportSection: "sec",
    Report: "rpt",
    CreatorPacket: "pkt",
    SubQuestion: "sq",
    ResearchPlan: "plan",
    ResearchState: "job",
}

# Models with a default-factory datetime field, mapped to those field names.
DATETIME_FIELDS: dict[type[BaseModel], tuple[str, ...]] = {
    Source: ("discovered_at",),
    Evidence: ("extracted_at",),
    Verdict: ("verified_at",),
    Finding: ("synthesized_at",),
    Critique: ("critiqued_at",),
    Report: ("published_at",),
    CreatorPacket: ("created_at",),
    ResearchPlan: ("created_at",),
    ResearchState: ("created_at", "updated_at"),
}

# Deterministic, non-flaky run: disable hypothesis's timing-based deadline (CI
# jitter would otherwise flake) and bound the example count.
_PROPERTY = settings(deadline=None, max_examples=50)


# ---------------------------------------------------------------------------
# Round-trip: model_validate(model_dump()) == m for every model.
# ---------------------------------------------------------------------------


@_PROPERTY
@pytest.mark.parametrize("cls", list(MODEL_STRATEGIES), ids=lambda c: c.__name__)
@given(data=st.data())
def test_model_roundtrips(cls: type[BaseModel], data: st.DataObject) -> None:
    model = data.draw(MODEL_STRATEGIES[cls])
    assert cls.model_validate(model.model_dump()) == model


# ---------------------------------------------------------------------------
# ID prefix invariant + uniqueness.
# ---------------------------------------------------------------------------


@_PROPERTY
@pytest.mark.parametrize(("cls", "prefix"), list(ID_PREFIXES.items()), ids=lambda v: str(v))
@given(data=st.data())
def test_minted_id_has_expected_prefix(
    cls: type[BaseModel], prefix: str, data: st.DataObject
) -> None:
    # The strategies leave `id` to the default factory, so the drawn model's id
    # exercises the live `_gen_id` path.
    minted: str = data.draw(MODEL_STRATEGIES[cls]).id  # type: ignore[attr-defined]
    assert minted.startswith(f"{prefix}_")
    # Suffix is the 16-hex-char token (`secrets.token_hex(8)`); the underscore
    # prefix delimiter is unambiguous because the suffix is hex-only.
    suffix = minted[len(prefix) + 1 :]
    assert len(suffix) == 16
    assert all(ch in "0123456789abcdef" for ch in suffix)


@_PROPERTY
@given(prefix=st.sampled_from(sorted(set(ID_PREFIXES.values()))), n=st.integers(2, 32))
def test_gen_id_is_unique_and_prefixed(prefix: str, n: int) -> None:
    ids = [_gen_id(prefix) for _ in range(n)]
    assert all(i.startswith(f"{prefix}_") for i in ids)
    assert len(set(ids)) == n  # 64 bits of entropy: no collisions across small n


# ---------------------------------------------------------------------------
# Strictness: extra='forbid' rejects any unknown key.
# ---------------------------------------------------------------------------


@_PROPERTY
@pytest.mark.parametrize("cls", list(MODEL_STRATEGIES), ids=lambda c: c.__name__)
@given(data=st.data(), key=st.text(min_size=1, max_size=12))
def test_unknown_keys_rejected(cls: type[BaseModel], data: st.DataObject, key: str) -> None:
    assume(key not in cls.model_fields)
    payload = data.draw(MODEL_STRATEGIES[cls]).model_dump()
    payload[key] = "unexpected"
    with pytest.raises(ValidationError):
        cls.model_validate(payload)


# ---------------------------------------------------------------------------
# Bounded confidence: Evidence.confidence / Verdict.confidence stay in [0, 1].
# (These are the only [0, 1]-constrained float fields in the schema.)
# ---------------------------------------------------------------------------


@_PROPERTY
@given(data=st.data(), bad=_BAD_CONFIDENCE)
def test_evidence_confidence_out_of_range_rejected(data: st.DataObject, bad: float) -> None:
    payload = data.draw(evidences()).model_dump()
    payload["confidence"] = bad
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


@_PROPERTY
@given(data=st.data(), bad=_BAD_CONFIDENCE)
def test_verdict_confidence_out_of_range_rejected(data: st.DataObject, bad: float) -> None:
    payload = data.draw(verdicts()).model_dump()
    payload["confidence"] = bad
    with pytest.raises(ValidationError):
        Verdict.model_validate(payload)


@_PROPERTY
@given(c=_CONFIDENCE)
def test_in_range_confidence_accepted(c: float) -> None:
    # The complement of the rejection tests: every value in [0, 1] is accepted.
    ev = Evidence(
        claim="x",
        source_id="src_a",
        source_url="https://x",
        chunk_id="chk_a",
        chunk_text="x",
        confidence=c,
        extracted_via="ext",
    )
    assert ev.confidence == c


# ---------------------------------------------------------------------------
# Timezone-aware timestamps: default-constructed datetime fields are tz-aware.
#
# Per ADR 0001 the datetime fields carry no validator forcing tzinfo (a naive
# input would be accepted), so the tz-aware guarantee is a property of the
# *default factory* (`datetime.now(UTC)`). The property below asserts that
# contract on freshly default-constructed models across the datetime-bearing
# classes. Generated datetimes are pinned to UTC by their strategy, so there is
# nothing to assert about them here (that would test hypothesis, not the schema).
# ---------------------------------------------------------------------------

_DEFAULT_CTORS: dict[type[BaseModel], object] = {
    Source: lambda: Source(url="https://x", type=SourceType.WEB, discovered_via="v"),
    Evidence: lambda: Evidence(
        claim="x",
        source_id="s",
        source_url="https://x",
        chunk_id="c",
        chunk_text="x",
        confidence=0.5,
        extracted_via="v",
    ),
    Verdict: lambda: Verdict(
        claim="x",
        support_level=SupportLevel.SINGLE_SOURCE,
        confidence=0.5,
        verified_via="v",
    ),
    Finding: lambda: Finding(
        statement="x",
        disputed=False,
        weakest_support=SupportLevel.SINGLE_SOURCE,
        synthesized_via="v",
    ),
    Critique: lambda: Critique(decision=CritiqueDecision.ACCEPT, rationale="x", critiqued_via="v"),
    Report: lambda: Report(title="t", abstract="a", published_via="v"),
    CreatorPacket: lambda: CreatorPacket(report_id="rpt_x", published_via="v"),
    ResearchPlan: ResearchPlan,
    ResearchState: lambda: ResearchState(topic="t"),
}


@pytest.mark.parametrize(("cls", "fields"), list(DATETIME_FIELDS.items()), ids=lambda v: str(v))
def test_default_timestamps_are_timezone_aware(
    cls: type[BaseModel], fields: tuple[str, ...]
) -> None:
    instance = _DEFAULT_CTORS[cls]()  # type: ignore[operator]
    for field in fields:
        value = getattr(instance, field)
        assert isinstance(value, datetime)
        assert value.tzinfo is not None
        assert value.utcoffset() == UTC.utcoffset(None)
