"""Tests for the ScriptBuilder creator-packet → ShortScript tool (ADR 0038).

Fully hermetic: `ScriptBuilder` is a pure, deterministic function of the packet
plus selection indices — no network, no LLM, no I/O, no clock-dependent assert.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.research_state import (
    CaveatKind,
    ContentAngle,
    CreatorPacket,
    CreatorWarning,
    HookIdea,
    KeyFact,
    NarrativeOption,
    SupportLevel,
)
from app.scripting.builder import (
    DEFAULT_LOOP_TEXT,
    SHORTS_CEILING_MS,
    SHORTS_FLOOR_MS,
    ScriptBuilder,
    ScriptBuilderError,
    _extract_visual_keyword,
    _relevant_warnings,
    _split_into_beats,
)
from app.scripting.schemas import BeatRole, ScriptBeat, ShortScript


def _packet(
    *,
    hooks: list[HookIdea] | None = None,
    narratives: list[NarrativeOption] | None = None,
    key_facts: list[KeyFact] | None = None,
    warnings: list[CreatorWarning] | None = None,
) -> CreatorPacket:
    return CreatorPacket(
        report_id="rpt_test",
        hooks=hooks
        if hooks is not None
        else [HookIdea(text="Did you know X?", finding_ids=["fnd_1"])],
        angles=[],
        narratives=narratives
        if narratives is not None
        else [
            NarrativeOption(
                title="The Arc",
                script_outline="First the setup.\nThen the twist.\nFinally the payoff.",
                finding_ids=["fnd_1", "fnd_2"],
            )
        ],
        key_facts=key_facts
        if key_facts is not None
        else [
            KeyFact(
                statement="X is true",
                finding_id="fnd_1",
                disputed=False,
                weakest_support=SupportLevel.CORROBORATED,
            ),
            KeyFact(
                statement="Y is contested",
                finding_id="fnd_2",
                disputed=True,
                weakest_support=SupportLevel.CONTRADICTED,
            ),
        ],
        warnings=warnings if warnings is not None else [],
        published_via="packet:fake",
    )


# --- happy path / structure -------------------------------------------------


def test_build_produces_ordered_four_beat_arc() -> None:
    packet = _packet()
    script = ScriptBuilder().build(packet)
    assert isinstance(script, ShortScript)
    roles = [beat.role for beat in script.beats]
    # hook, two build lines, the last line as payoff, then loop (3 topical lines)
    assert roles == [
        BeatRole.HOOK,
        BeatRole.BUILD,
        BeatRole.BUILD,
        BeatRole.PAYOFF,
        BeatRole.LOOP,
    ]
    assert script.beats[0].text == "Did you know X?"
    assert script.beats[1].text == "First the setup."
    assert script.beats[3].text == "Finally the payoff."
    assert script.beats[-1].text == DEFAULT_LOOP_TEXT
    assert script.narrative_title == "The Arc"
    assert script.source_packet_id == packet.id
    assert script.built_via == "scripting:scriptbuilder"


def test_arc_roles_scale_with_topical_line_count() -> None:
    # n>=2 topical lines -> [HOOK, BUILD*(n-1), PAYOFF, LOOP]
    five = NarrativeOption(
        title="Five",
        script_outline="l1\nl2\nl3\nl4\nl5",
        finding_ids=["fnd_1"],
    )
    script = ScriptBuilder().build(_packet(narratives=[five]))
    roles = [beat.role for beat in script.beats]
    assert roles == [
        BeatRole.HOOK,
        BeatRole.BUILD,
        BeatRole.BUILD,
        BeatRole.BUILD,
        BeatRole.BUILD,
        BeatRole.PAYOFF,
        BeatRole.LOOP,
    ]
    # the payoff is the final topical line
    assert script.beats[-2].role is BeatRole.PAYOFF
    assert script.beats[-2].text == "l5"


def test_single_topical_line_has_payoff_but_no_build() -> None:
    # exactly 1 topical line → [HOOK, PAYOFF, LOOP] (no BUILD beat)
    one = NarrativeOption(title="One", script_outline="the only line", finding_ids=["fnd_1"])
    script = ScriptBuilder().build(_packet(narratives=[one]))
    roles = [beat.role for beat in script.beats]
    assert roles == [BeatRole.HOOK, BeatRole.PAYOFF, BeatRole.LOOP]
    assert not any(b.role is BeatRole.BUILD for b in script.beats)
    assert script.beats[1].text == "the only line"


def test_every_beat_text_is_single_line() -> None:
    # one beat == one MediaPipeline narration segment / caption cue
    script = ScriptBuilder().build(_packet())
    for beat in script.beats:
        assert "\n" not in beat.text


def test_each_beat_has_positive_duration_and_visual_keyword() -> None:
    script = ScriptBuilder().build(_packet())
    for beat in script.beats:
        assert beat.estimated_duration_ms > 0
        assert beat.visual_keyword


def test_total_is_sum_of_beat_estimates() -> None:
    script = ScriptBuilder().build(_packet())
    assert script.total_estimated_ms == sum(b.estimated_duration_ms for b in script.beats)


def test_is_deterministic_for_same_input() -> None:
    packet = _packet()
    a = ScriptBuilder().build(packet)
    b = ScriptBuilder().build(packet)
    assert a.model_dump(exclude={"id"}) == b.model_dump(exclude={"id"})


# --- grounding / §11 honesty ------------------------------------------------


_TOPICAL_ROLES = (BeatRole.BUILD, BeatRole.PAYOFF)


def test_topical_beats_inherit_narrative_finding_ids() -> None:
    script = ScriptBuilder().build(_packet())
    topical = [b for b in script.beats if b.role in _TOPICAL_ROLES]
    for beat in topical:
        assert beat.finding_ids == ["fnd_1", "fnd_2"]


def test_disputed_finding_flags_topical_beats() -> None:
    # fnd_2 is disputed in the default packet; the narrative cites it.
    script = ScriptBuilder().build(_packet())
    topical = [b for b in script.beats if b.role in _TOPICAL_ROLES]
    assert all(beat.disputed for beat in topical)


def test_undisputed_hook_is_not_flagged() -> None:
    # the hook cites only fnd_1 (not disputed)
    script = ScriptBuilder().build(_packet())
    hook = script.beats[0]
    assert hook.finding_ids == ["fnd_1"]
    assert hook.disputed is False


def test_loop_is_claim_free_and_never_disputed() -> None:
    # even when every topical finding is disputed, the LOOP stays clean
    facts = [
        KeyFact(
            statement="all disputed",
            finding_id="fnd_1",
            disputed=True,
            weakest_support=SupportLevel.CONTRADICTED,
        ),
        KeyFact(
            statement="also disputed",
            finding_id="fnd_2",
            disputed=True,
            weakest_support=SupportLevel.CONTRADICTED,
        ),
    ]
    script = ScriptBuilder().build(_packet(key_facts=facts))
    loop = script.beats[-1]
    assert loop.role is BeatRole.LOOP
    assert loop.finding_ids == []
    assert loop.disputed is False


def test_relevant_warnings_carried_forward_when_findings_overlap() -> None:
    warning = CreatorWarning(
        kind=CaveatKind.DISPUTED_FINDING,
        detail="fnd_2 rests on contradictory sources",
        finding_ids=["fnd_2"],
    )
    script = ScriptBuilder().build(_packet(warnings=[warning]))
    assert script.warnings == [warning]


def test_unrelated_warning_is_not_carried_forward() -> None:
    # a warning about a finding no beat uses must not ride along
    warning = CreatorWarning(
        kind=CaveatKind.WEAK_SUPPORT,
        detail="fnd_99 is single-source",
        finding_ids=["fnd_99"],
    )
    script = ScriptBuilder().build(_packet(warnings=[warning]))
    assert script.warnings == []


def test_disputed_via_key_facts_not_warnings() -> None:
    # no warnings at all, but a disputed KeyFact must still flag the beats
    script = ScriptBuilder().build(_packet(warnings=[]))
    topical = [b for b in script.beats if b.role in _TOPICAL_ROLES]
    assert all(b.disputed for b in topical)
    assert script.warnings == []


# --- timing / ceiling -------------------------------------------------------


def test_short_script_does_not_exceed_ceiling() -> None:
    script = ScriptBuilder().build(_packet())
    assert script.exceeds_shorts_ceiling is False
    assert script.target_duration_ms == script.total_estimated_ms
    assert script.target_duration_ms <= SHORTS_CEILING_MS


def test_overflow_is_flagged_not_scaled_or_raised() -> None:
    # a very long outline overflows 60s; estimates stay honest, target clamps
    long_line = " ".join(["word"] * 400)
    narrative = NarrativeOption(
        title="Long", script_outline=f"{long_line}\n{long_line}", finding_ids=["fnd_1"]
    )
    script = ScriptBuilder().build(_packet(narratives=[narrative]))
    assert script.total_estimated_ms > SHORTS_CEILING_MS
    assert script.exceeds_shorts_ceiling is True
    # target clamps to the ceiling; per-beat estimates are NOT scaled down
    assert script.target_duration_ms == SHORTS_CEILING_MS
    assert sum(b.estimated_duration_ms for b in script.beats) == script.total_estimated_ms


def test_ceiling_boundary_not_flagged_at_exact_ceiling() -> None:
    # total == ceiling is NOT an overflow (strict >), unchanged from ADR 0038
    script = ScriptBuilder().build(_packet(narratives=[_long_narrative(200)]))
    total = script.total_estimated_ms
    at_ceiling = ScriptBuilder(shorts_ceiling_ms=total).build(
        _packet(narratives=[_long_narrative(200)])
    )
    assert at_ceiling.exceeds_shorts_ceiling is False  # total == ceiling → not exceeding
    assert at_ceiling.target_duration_ms == total
    just_under_ceiling = ScriptBuilder(shorts_ceiling_ms=total - 1).build(
        _packet(narratives=[_long_narrative(200)])
    )
    assert just_under_ceiling.exceeds_shorts_ceiling is True  # total > ceiling → exceeding


def test_words_per_minute_knob_changes_estimate() -> None:
    packet = _packet()
    slow = ScriptBuilder(words_per_minute=75).build(packet)
    fast = ScriptBuilder(words_per_minute=300).build(packet)
    assert slow.total_estimated_ms > fast.total_estimated_ms


def test_custom_loop_text_is_used() -> None:
    script = ScriptBuilder(loop_text="Rewatch the opener.").build(_packet())
    assert script.beats[-1].text == "Rewatch the opener."
    assert script.beats[-1].role is BeatRole.LOOP


def test_cta_text_is_a_deprecated_alias_for_loop_text() -> None:
    # existing callers passing cta_text= keep working: it fills the LOOP beat.
    script = ScriptBuilder(cta_text="Subscribe now!").build(_packet())
    assert script.beats[-1].text == "Subscribe now!"
    assert script.beats[-1].role is BeatRole.LOOP


def test_loop_text_takes_precedence_over_cta_text() -> None:
    script = ScriptBuilder(loop_text="loop wins", cta_text="cta loses").build(_packet())
    assert script.beats[-1].text == "loop wins"


def test_default_loop_text_when_neither_given() -> None:
    script = ScriptBuilder().build(_packet())
    assert script.beats[-1].text == DEFAULT_LOOP_TEXT


def test_invalid_constructor_knobs_raise() -> None:
    with pytest.raises(ValueError):
        ScriptBuilder(words_per_minute=0)
    with pytest.raises(ValueError):
        ScriptBuilder(shorts_ceiling_ms=0)
    with pytest.raises(ValueError):
        ScriptBuilder(shorts_floor_ms=0)
    with pytest.raises(ValueError, match="must not exceed"):
        ScriptBuilder(shorts_floor_ms=90_000, shorts_ceiling_ms=60_000)


# --- timing / floor (length band) -------------------------------------------


def _long_narrative(word_count: int) -> NarrativeOption:
    # one topical line of `word_count` real words → deterministic WPM estimate
    return NarrativeOption(
        title="Long", script_outline=" ".join(["word"] * word_count), finding_ids=["fnd_1"]
    )


def test_below_floor_is_flagged_true_when_too_thin() -> None:
    # the default packet's short outline lands well under the 45s floor
    script = ScriptBuilder().build(_packet())
    assert script.total_estimated_ms < SHORTS_FLOOR_MS
    assert script.below_shorts_floor is True


def test_at_or_above_floor_is_not_flagged() -> None:
    # ~45s at 150 wpm ≈ 112.5 words; 120 topical words lands in-band (< 60s)
    script = ScriptBuilder().build(_packet(narratives=[_long_narrative(120)]))
    assert SHORTS_FLOOR_MS <= script.total_estimated_ms <= SHORTS_CEILING_MS
    assert script.below_shorts_floor is False
    assert script.exceeds_shorts_ceiling is False


def test_floor_boundary_is_below_only_when_strictly_under() -> None:
    # exact-estimate floor → at-floor is NOT below; a hair above the estimate is.
    script = ScriptBuilder().build(_packet(narratives=[_long_narrative(120)]))
    total = script.total_estimated_ms
    at_floor = ScriptBuilder(shorts_floor_ms=total).build(
        _packet(narratives=[_long_narrative(120)])
    )
    assert at_floor.below_shorts_floor is False  # total == floor → not below
    just_over_floor = ScriptBuilder(shorts_floor_ms=total + 1).build(
        _packet(narratives=[_long_narrative(120)])
    )
    assert just_over_floor.below_shorts_floor is True  # total < floor → below


# --- selection / errors -----------------------------------------------------


def test_selects_requested_hook_and_narrative_indices() -> None:
    packet = _packet(
        hooks=[
            HookIdea(text="hook A", finding_ids=["fnd_1"]),
            HookIdea(text="hook B", finding_ids=["fnd_1"]),
        ],
        narratives=[
            NarrativeOption(title="N0", script_outline="a", finding_ids=["fnd_1"]),
            NarrativeOption(title="N1", script_outline="b", finding_ids=["fnd_1"]),
        ],
    )
    script = ScriptBuilder().build(packet, hook_index=1, narrative_index=1)
    assert script.beats[0].text == "hook B"
    assert script.narrative_title == "N1"


def test_no_hooks_raises() -> None:
    with pytest.raises(ScriptBuilderError, match="no hooks"):
        ScriptBuilder().build(_packet(hooks=[]))


def test_no_narratives_raises() -> None:
    with pytest.raises(ScriptBuilderError, match="no narrative options"):
        ScriptBuilder().build(_packet(narratives=[]))


def test_out_of_range_hook_index_raises() -> None:
    with pytest.raises(ScriptBuilderError, match="hook_index 5 out of range"):
        ScriptBuilder().build(_packet(), hook_index=5)


def test_out_of_range_narrative_index_raises() -> None:
    with pytest.raises(ScriptBuilderError, match="narrative_index 5 out of range"):
        ScriptBuilder().build(_packet(), narrative_index=5)


def test_blank_narrative_outline_raises() -> None:
    narrative = NarrativeOption(title="Empty", script_outline="\n  \n\t\n", finding_ids=["fnd_1"])
    with pytest.raises(ScriptBuilderError, match="no narratable script beats"):
        ScriptBuilder().build(_packet(narratives=[narrative]))


# --- helpers ----------------------------------------------------------------


def test_split_into_beats_drops_blank_lines_and_strips() -> None:
    assert _split_into_beats("  one  \n\n two\n\t\nthree ") == ["one", "two", "three"]


def test_extract_visual_keyword_strips_stopwords() -> None:
    assert _extract_visual_keyword("The history of the Roman empire") == "history roman empire"


def test_extract_visual_keyword_falls_back_for_all_stopwords() -> None:
    # all-stopword line falls back to the first token, lowercased
    assert _extract_visual_keyword("the and of") == "the"


def test_extract_visual_keyword_handles_empty() -> None:
    assert _extract_visual_keyword("   ") == "abstract background"


def test_relevant_warnings_helper_intersection() -> None:
    w1 = CreatorWarning(kind=CaveatKind.DISPUTED_FINDING, detail="d", finding_ids=["fnd_1"])
    w2 = CreatorWarning(kind=CaveatKind.WEAK_SUPPORT, detail="w", finding_ids=["fnd_9"])
    assert _relevant_warnings([w1, w2], {"fnd_1"}) == [w1]


def test_beat_is_strict() -> None:
    with pytest.raises(ValidationError):
        ScriptBeat(  # type: ignore[call-arg]
            role=BeatRole.BUILD,
            text="x",
            estimated_duration_ms=1,
            visual_keyword="x",
            bogus="nope",
        )


# --- back-compat: deprecated BODY/CTA roles still deserialize ----------------


def test_deprecated_body_cta_roles_still_deserialize() -> None:
    # persisted ShortScripts may carry the old (deprecated) BODY/CTA role values;
    # ADR 0061 keeps them on BeatRole so those records still round-trip.
    assert BeatRole("body") is BeatRole.BODY
    assert BeatRole("cta") is BeatRole.CTA

    legacy = ShortScript(
        source_packet_id="pkt_legacy",
        narrative_title="Legacy",
        beats=[
            ScriptBeat(role=BeatRole.HOOK, text="h", estimated_duration_ms=1, visual_keyword="h"),
            ScriptBeat(role=BeatRole.BODY, text="b", estimated_duration_ms=1, visual_keyword="b"),
            ScriptBeat(role=BeatRole.CTA, text="c", estimated_duration_ms=1, visual_keyword="c"),
        ],
        total_estimated_ms=3,
        target_duration_ms=3,
        built_via="scripting:scriptbuilder",
    )
    round_tripped = ShortScript.model_validate(legacy.model_dump())
    assert [b.role for b in round_tripped.beats] == [
        BeatRole.HOOK,
        BeatRole.BODY,
        BeatRole.CTA,
    ]
    # a JSON round-trip (serialized StrEnum values) also survives
    from_json = ShortScript.model_validate_json(legacy.model_dump_json())
    assert from_json.beats[1].role is BeatRole.BODY
    assert from_json.beats[2].role is BeatRole.CTA


def test_angle_input_is_unused_but_tolerated() -> None:
    # angles are not part of the script structure (v1); a packet with angles builds fine
    packet = _packet()
    packet.angles = [ContentAngle(angle="A", rationale="R", finding_ids=["fnd_1"])]
    script = ScriptBuilder().build(packet)
    assert script.beats[0].role is BeatRole.HOOK
