"""Tests for the MediaPipeline creator-packet → media handoff (ADR 0025).

Fully hermetic: the `FakeTTSProvider`, `FakeCompositionService`, and
`FakeWordAligner` seams plus the real `DeterministicSubtitleService` (pure, no
fake needed). No network, no ffmpeg, no aeneas.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from itertools import pairwise

import pytest

from app.media.alignment.base import AlignmentError, FakeWordAligner
from app.media.composition.base import FakeCompositionService
from app.media.pipeline import (
    MAX_PLAUSIBLE_WORDS_PER_SECOND,
    MediaPipeline,
    MediaPipelineError,
    MediaPlan,
    _allocate_timings,
    _derive_timings_from_alignment,
    _implausible_segment_indices,
    _split_into_beats,
)
from app.media.schemas import DEFAULT_CAPTION_STYLE, CaptionStyle, WordSpan
from app.media.tts.base import FakeTTSProvider
from app.schemas.research_state import CreatorPacket, NarrativeOption


def _packet(*narratives: NarrativeOption) -> CreatorPacket:
    return CreatorPacket(
        report_id="rpt_test",
        narratives=list(narratives),
        published_via="packet:fake",
    )


def _narrative(title: str, script_outline: str) -> NarrativeOption:
    return NarrativeOption(title=title, script_outline=script_outline, finding_ids=["fnd_x"])


def _build(packet: CreatorPacket, **kwargs: object) -> MediaPlan:
    pipeline = MediaPipeline(FakeTTSProvider(ms_per_char=10), FakeCompositionService())
    return asyncio.run(pipeline.build(packet, **kwargs))  # type: ignore[arg-type]


# --- _split_into_beats -------------------------------------------------------


def test_split_drops_blank_lines_and_strips() -> None:
    assert _split_into_beats("  hook  \n\n  body \n   \nclose") == ["hook", "body", "close"]


def test_split_empty_outline_is_empty() -> None:
    assert _split_into_beats("   \n  \n") == []


# --- _allocate_timings (the correctness anchor) ------------------------------


def test_timings_are_contiguous_and_cover_total_exactly() -> None:
    segments = ["aa", "bbbb", "cc"]  # lengths 2, 4, 2 over 800ms
    timings = _allocate_timings(segments, 800)
    assert timings[0][0] == 0
    assert timings[-1][1] == 800  # last boundary == total, no drift
    # contiguous: each start equals the previous end
    for (_, e_prev), (s_next, _) in pairwise(timings):
        assert e_prev == s_next
    # proportional to length: 2/8, 4/8, 2/8 of 800 -> 200, 400, 200 ms wide
    assert timings == [(0, 200), (200, 600), (600, 800)]


def test_timings_no_rounding_drift_on_awkward_total() -> None:
    segments = ["x", "y", "z"]  # equal lengths, 100ms doesn't divide by 3
    timings = _allocate_timings(segments, 100)
    assert timings[0][0] == 0
    assert timings[-1][1] == 100
    for (_, e_prev), (s_next, _) in pairwise(timings):
        assert e_prev == s_next


# --- MediaPipeline.build -----------------------------------------------------


def test_build_produces_media_plan_with_provenance() -> None:
    plan = _build(_packet(_narrative("Arc A", "hook\nbody\nclose")))
    assert isinstance(plan, MediaPlan)
    assert plan.narrative_title == "Arc A"
    assert plan.script_segments == ["hook", "body", "close"]
    assert plan.id.startswith("plan_")
    assert plan.produced_via == "media:pipeline"
    assert plan.audio.produced_via == "tts:fake"
    assert plan.captions.produced_via == "subtitles:deterministic"
    assert plan.video.produced_via == "composition:fake"


def test_build_holds_the_timing_invariant() -> None:
    plan = _build(_packet(_narrative("Arc", "alpha\nbeta gamma\ndelta")))
    # The ADR 0025 invariant: captions end exactly at audio end == video end.
    assert plan.captions.cues[-1].end_ms == plan.audio.duration_ms
    assert plan.video.duration_ms == plan.audio.duration_ms
    assert plan.captions.cues[0].start_ms == 0
    assert len(plan.captions.cues) == len(plan.script_segments)


def test_build_rejoins_to_source_packet() -> None:
    packet = _packet(_narrative("Arc", "one\ntwo"))
    plan = _build(packet)
    assert plan.source_packet_id == packet.id


def test_build_synthesizes_whole_narration_once() -> None:
    tts = FakeTTSProvider(ms_per_char=10)
    pipeline = MediaPipeline(tts, FakeCompositionService(), voice="alice")
    asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo\nthree"))))
    assert len(tts.calls) == 1  # one synthesis for the whole script, not per-beat
    assert tts.calls[0].voice == "alice"
    assert tts.calls[0].text == "one\ntwo\nthree"


def test_build_passes_visual_uris_through_to_composition() -> None:
    composition = FakeCompositionService()
    pipeline = MediaPipeline(FakeTTSProvider(), composition)
    uris = ["s3://clip1.mp4", "s3://clip2.mp4"]
    asyncio.run(pipeline.build(_packet(_narrative("Arc", "x")), visual_uris=uris))
    assert composition.calls[0].visual_uris == uris


def test_build_default_visual_uris_is_empty() -> None:
    composition = FakeCompositionService()
    pipeline = MediaPipeline(FakeTTSProvider(), composition)
    asyncio.run(pipeline.build(_packet(_narrative("Arc", "x"))))
    assert composition.calls[0].visual_uris == []


# --- explicit `segments` override (ADR 0063: the ScriptBuilder full-arc seam) -


def test_build_uses_explicit_segments_instead_of_splitting_the_outline() -> None:
    # When the caller (VideoPipeline) supplies pre-built beat texts (e.g. the
    # full HOOK/BUILD/PAYOFF/LOOP arc from ScriptBuilder), MediaPipeline must
    # narrate/caption exactly those — never fall back to re-splitting the
    # narrative's own script_outline, which the caller has deliberately bypassed.
    plan = _build(
        _packet(_narrative("Arc", "ignored outline line")),
        segments=["hook text", "build one", "payoff", "loop text"],
    )
    assert plan.script_segments == ["hook text", "build one", "payoff", "loop text"]
    assert len(plan.captions.cues) == 4


def test_build_segments_none_falls_back_to_narrative_outline() -> None:
    # The default (None) reproduces the pre-ADR-0063 behavior exactly.
    plan = _build(_packet(_narrative("Arc", "one\ntwo")), segments=None)
    assert plan.script_segments == ["one", "two"]


# --- explicit `caption_style` pass-through (ADR 0059, wired here per ADR 0063) -


def test_build_default_caption_style_reaches_composition() -> None:
    composition = FakeCompositionService()
    pipeline = MediaPipeline(FakeTTSProvider(), composition)
    asyncio.run(pipeline.build(_packet(_narrative("Arc", "x"))))
    assert composition.calls[0].caption_style is DEFAULT_CAPTION_STYLE


def test_build_custom_caption_style_reaches_composition() -> None:
    composition = FakeCompositionService()
    pipeline = MediaPipeline(FakeTTSProvider(), composition)
    style = CaptionStyle(font_name="Impact", font_size=64)
    asyncio.run(pipeline.build(_packet(_narrative("Arc", "x")), caption_style=style))
    assert composition.calls[0].caption_style == style


def test_build_selects_narrative_by_index() -> None:
    plan = _build(
        _packet(_narrative("first", "a"), _narrative("second", "b")),
        narrative_index=1,
    )
    assert plan.narrative_title == "second"


def test_build_skips_blank_beats_but_renders_the_rest() -> None:
    plan = _build(_packet(_narrative("Arc", "real line\n\n   \nanother")))
    assert plan.script_segments == ["real line", "another"]


def test_build_raises_when_no_narratives() -> None:
    with pytest.raises(MediaPipelineError, match="no narrative options"):
        _build(_packet())


def test_build_raises_when_narrative_index_out_of_range() -> None:
    with pytest.raises(MediaPipelineError, match="out of range"):
        _build(_packet(_narrative("Arc", "x")), narrative_index=5)


def test_build_raises_when_narrative_has_no_narratable_beat() -> None:
    with pytest.raises(MediaPipelineError, match="no narratable script segments"):
        _build(_packet(_narrative("Empty", "   \n\n  ")))


# --- optional word alignment (karaoke carrier wiring, ADR 0062) --------------


class _RaisingAligner:
    """A `WordAligner` stub whose every call fails (the degrade-path trigger)."""

    name = "raising"

    def __init__(self) -> None:
        self.calls = 0

    async def align(self, *, audio_path: str, segments: Sequence[str]) -> list[list[WordSpan]]:
        self.calls += 1
        raise AlignmentError("aeneas not installed")


def test_build_without_aligner_leaves_cues_word_free() -> None:
    # Default None -> exactly the pre-ADR-0062 behavior: no karaoke carrier.
    plan = _build(_packet(_narrative("Arc", "hook\nbody")))
    assert all(cue.words == [] for cue in plan.captions.cues)


def test_build_with_aligner_attaches_word_timings() -> None:
    # ms_per_word=200 (not FakeWordAligner's own default of 300, and not 50 or
    # 100) is deliberate, satisfying two independent constraints at once:
    # (1) ADR 0065 — it keeps this fake aligner's running clock inside the
    #     FakeTTSProvider's declared audio.duration_ms (1300ms for this
    #     narration at ms_per_char=100), which `_derive_timings_from_alignment`
    #     requires to succeed instead of falling back.
    # (2) ADR 0066 (issue #154) — its implied rate (1000/200 = 5 words/sec,
    #     the same for every segment since `FakeWordAligner` paces one word
    #     per `ms_per_word` with no gaps) stays under the 8 wps plausibility
    #     guard, so this test still exercises the derive-and-attach path
    #     rather than being redirected to the new per-segment fallback.
    aligner = FakeWordAligner(ms_per_word=200)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=100), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one two\nthree"))))

    assert [len(cue.words) for cue in plan.captions.cues] == [2, 1]
    assert [w.text for w in plan.captions.cues[0].words] == ["one", "two"]
    # The aligner was asked exactly once, for the narration audio + the beats.
    assert len(aligner.calls) == 1
    assert aligner.calls[0].audio_path == plan.audio.audio_uri
    assert aligner.calls[0].segments == ["one two", "three"]
    # ADR 0065: cue boundaries come from the SAME alignment, not a char-count
    # guess — one(0-200) two(200-400) -> cue 0 spans [0, 400); three(400-600),
    # last cue's end pinned to the true audio duration (1300), not 600.
    assert (plan.captions.cues[0].start_ms, plan.captions.cues[0].end_ms) == (0, 400)
    assert (plan.captions.cues[1].start_ms, plan.captions.cues[1].end_ms) == (400, 1300)


def test_build_degrades_when_aligner_raises(caplog: pytest.LogCaptureFixture) -> None:
    aligner = _RaisingAligner()
    pipeline = MediaPipeline(FakeTTSProvider(), FakeCompositionService(), word_aligner=aligner)
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    # The render completed on the cue-level path; nothing was attached.
    assert aligner.calls == 1
    assert plan.video.produced_via == "composition:fake"
    assert all(cue.words == [] for cue in plan.captions.cues)
    assert any("word alignment failed" in record.getMessage() for record in caplog.records)


def test_build_degrades_when_aligner_miscounts_segments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _MiscountingAligner:
        name = "miscounting"

        async def align(self, *, audio_path: str, segments: Sequence[str]) -> list[list[WordSpan]]:
            return [[]]  # one timing list for two segments: a broken contract

    pipeline = MediaPipeline(
        FakeTTSProvider(), FakeCompositionService(), word_aligner=_MiscountingAligner()
    )
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    # Nothing half-attached: the count check runs before any cue is touched.
    assert all(cue.words == [] for cue in plan.captions.cues)
    assert any("word alignment failed" in record.getMessage() for record in caplog.records)


# --- `_derive_timings_from_alignment` (ADR 0065, issue #152's fix) -----------
#
# Pure, hermetic: fixture `WordSpan` lists in, `(start_ms, end_ms)` tuples (or
# `None`) out — no aligner, no audio, no pipeline needed.


def _span(text: str, start_ms: int, end_ms: int) -> WordSpan:
    return WordSpan(text=text, start_ms=start_ms, end_ms=end_ms)


def test_derive_timings_uses_real_word_boundaries_not_char_proportions() -> None:
    # "one" is a third of the text by length but two-thirds of the real audio
    # by duration — exactly the #152 failure mode (a segment whose real
    # speech share disagrees with its character-count share).
    word_lists = [[_span("one", 0, 60)], [_span("two", 60, 70)]]
    assert _derive_timings_from_alignment(word_lists, total_ms=70) == [(0, 60), (60, 70)]


def test_derive_timings_bridges_a_real_silence_gap() -> None:
    # Segment 0's last word ends at 40ms; segment 1's first word doesn't start
    # until 55ms (a real ~15ms silence, mirroring SegmentedTTSProvider's
    # inter-sentence pauses, #150). The recommended default bridges the gap
    # forward so the caption track has no dead, caption-free air.
    word_lists = [[_span("hello", 0, 40)], [_span("world", 55, 90)]]
    timings = _derive_timings_from_alignment(word_lists, total_ms=90)
    assert timings == [(0, 55), (55, 90)]
    # Touching, not overlapping: cue 0's end is exactly cue 1's start.
    assert timings[0][1] == timings[1][0]


def test_derive_timings_bridges_multiple_gaps_without_drift() -> None:
    # Three segments, two independent gaps (30->45 and 70->90). Bridging one
    # gap must not perturb a non-adjacent boundary — no compounding drift.
    word_lists = [[_span("a", 0, 30)], [_span("b", 45, 70)], [_span("c", 90, 120)]]
    timings = _derive_timings_from_alignment(word_lists, total_ms=130)
    assert timings == [(0, 45), (45, 90), (90, 130)]
    for (_, e_prev), (s_next, _) in pairwise(timings):
        assert e_prev == s_next  # every seam touches exactly, never overlaps


def test_derive_timings_pins_first_start_to_zero() -> None:
    # The aligner reports 20ms of unassigned lead-in silence before the first
    # word; the derived first cue must still start at 0 (full coverage, no
    # uncaptioned head), exactly like `_allocate_timings` guarantees.
    word_lists = [[_span("a", 20, 50)], [_span("b", 50, 90)]]
    timings = _derive_timings_from_alignment(word_lists, total_ms=120)
    assert timings is not None
    assert timings[0][0] == 0


def test_derive_timings_pins_last_end_to_audio_duration() -> None:
    # The aligner's last word ends at 90ms but the real audio runs to 120ms
    # (trailing silence); the derived last cue must still reach total_ms
    # exactly, never leaving a dead, uncaptioned tail.
    word_lists = [[_span("a", 20, 50)], [_span("b", 50, 90)]]
    timings = _derive_timings_from_alignment(word_lists, total_ms=120)
    assert timings is not None
    assert timings[-1][1] == 120


def test_derive_timings_single_segment_spans_the_whole_audio() -> None:
    assert _derive_timings_from_alignment([[_span("hi", 5, 40)]], total_ms=100) == [(0, 100)]


def test_derive_timings_none_for_empty_word_lists() -> None:
    assert _derive_timings_from_alignment([], total_ms=100) is None


def test_derive_timings_none_when_any_segment_has_no_words() -> None:
    # Count matches (2 lists for 2 segments) but one segment's list is empty —
    # an aligner contract oddity, not a normal non-blank-text outcome. Must
    # fall back for the WHOLE result, never derive the other segment alone.
    word_lists = [[_span("hi", 0, 50)], []]
    assert _derive_timings_from_alignment(word_lists, total_ms=100) is None


def test_derive_timings_none_on_real_overlap_between_segments() -> None:
    # Segment 1's first word starts (50) before segment 0's last word ends
    # (60) — an alignment anomaly (a narrator can't speak two segments at
    # once), never a silence gap. Bridging would produce an overlapping cue
    # pair, which is disallowed, so the whole result must degrade to None.
    word_lists = [[_span("hello", 0, 60)], [_span("world", 50, 90)]]
    assert _derive_timings_from_alignment(word_lists, total_ms=90) is None


# --- Build-level proof: derived boundaries reach the real `MediaPlan` -------


class _FixedWordAligner:
    """A `WordAligner` stub returning pre-built spans regardless of input.

    Unlike `FakeWordAligner`'s synthetic per-word cadence (independent of
    `audio.duration_ms`), this lets a test hand-craft word timings that
    deliberately disagree with `_allocate_timings`'s character-count guess —
    the direct proof this PR's regression test needs.
    """

    name = "fixed"

    def __init__(self, word_lists: list[list[WordSpan]]) -> None:
        self._word_lists = word_lists
        self.calls = 0

    async def align(self, *, audio_path: str, segments: Sequence[str]) -> list[list[WordSpan]]:
        self.calls += 1
        return self._word_lists


def test_build_cue_boundaries_match_alignment_not_the_char_count_guess() -> None:
    """The load-bearing regression test for #152 / ADR 0065.

    Before this PR, `MediaPipeline.build` always used `_allocate_timings`'s
    character-count guess for cue boundaries, regardless of whether a
    `word_aligner` was configured — this test's assertions on
    `cue.start_ms`/`end_ms` would have FAILED against that code, because the
    guess and the real alignment disagree by design here: "one" is half the
    text by character count but the aligner says it is 6/7ths of the real
    audio by duration (mirrors #152's measured pattern of a segment whose
    real speech share diverges sharply from its character-count share). Both
    segments' own implied rate (900ms/word and 150ms/word respectively) stays
    well under ADR 0066's 8 wps plausibility guard (issue #154), so this test
    exercises the plain derive-and-attach path, not the new fallback.
    """
    word_lists = [[_span("one", 0, 900)], [_span("two", 900, 1050)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=150), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    # The pre-fix guess, spelled out so the contrast with the assertions below
    # is explicit (this is exactly what pre-fix code would have produced).
    guessed = _allocate_timings(["one", "two"], plan.audio.duration_ms)
    assert guessed == [(0, 525), (525, 1050)]

    cues = plan.captions.cues
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 900)
    assert (cues[1].start_ms, cues[1].end_ms) == (900, 1050)
    assert (cues[0].start_ms, cues[0].end_ms) != guessed[0]
    assert (cues[1].start_ms, cues[1].end_ms) != guessed[1]
    # The karaoke carrier comes from the identical source as the boundaries.
    assert [w.text for w in cues[0].words] == ["one"]
    assert [w.text for w in cues[1].words] == ["two"]


def test_build_pins_endpoints_even_when_alignment_falls_short() -> None:
    # The aligner's first word starts at 100ms (not 0) and its last word ends
    # at 1000ms while the real audio runs to 1100ms (trailing silence) — the
    # derived first/last cue must still hit the exact endpoints. Each
    # segment's own implied rate (2-2.5 wps) stays well under ADR 0066's 8 wps
    # plausibility guard (issue #154), so this exercises the plain derive path.
    word_lists = [[_span("hello", 100, 600)], [_span("world", 600, 1000)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=100), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "hello\nworld"))))

    assert plan.captions.cues[0].start_ms == 0
    assert plan.captions.cues[-1].end_ms == plan.audio.duration_ms == 1100


def test_build_falls_back_fully_when_one_segment_has_no_aligned_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Alignment "succeeds" (the count matches: 2 lists for 2 segments) but one
    # segment's word list is empty. Per ADR 0065 this must degrade exactly
    # like a full alignment failure: `_allocate_timings` for BOTH cues (never
    # a mix), and no cue keeps any word timings at all.
    word_lists = [[_span("one", 0, 40)], []]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    expected = _allocate_timings(["one", "two"], plan.audio.duration_ms)
    actual = [(c.start_ms, c.end_ms) for c in plan.captions.cues]
    assert actual == expected
    assert all(cue.words == [] for cue in plan.captions.cues)
    assert any("could not be reconciled" in record.getMessage() for record in caplog.records)


def test_build_falls_back_fully_on_real_overlap_between_segments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A build()-level regression companion to
    # `test_derive_timings_none_on_real_overlap_between_segments`: this
    # existing ADR 0065 total-failure trigger (a genuine overlap, not a
    # plausibility problem) must still short-circuit before ADR 0066's
    # plausibility guard ever runs, exactly as it did before this PR.
    word_lists = [[_span("hello", 0, 60)], [_span("world", 50, 90)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "hello\nworld"))))

    expected = _allocate_timings(["hello", "world"], plan.audio.duration_ms)
    actual = [(c.start_ms, c.end_ms) for c in plan.captions.cues]
    assert actual == expected
    assert all(cue.words == [] for cue in plan.captions.cues)
    assert any("could not be reconciled" in record.getMessage() for record in caplog.records)
    # The ADR 0066 plausibility warning never fires: the total-failure branch
    # returned before `_implausible_segment_indices` was even called.
    assert not any("impossible speaking rate" in record.getMessage() for record in caplog.records)


# --- `_implausible_segment_indices` (ADR 0066's detector, issue #154) -------
#
# Pure, hermetic: fixture `WordSpan` lists in, a `set[int]` of flagged indices
# out — no aligner, no audio, no pipeline needed. Mirrors the
# `_derive_timings_from_alignment` pure-function test section above.


def test_implausible_flags_a_segment_far_above_the_threshold() -> None:
    # Mirrors #154's own measured numbers: 11 words crushed into ~44ms is a
    # ~250 words/sec implied rate, more than an order of magnitude past the
    # 8 wps guard.
    word_lists = [[_span(f"w{i}", i * 4, (i + 1) * 4) for i in range(11)]]
    assert _implausible_segment_indices(word_lists) == {0}


def test_implausible_does_not_flag_a_plausible_fast_segment() -> None:
    # 11 words over 1.68s (~6.5 wps, #154's own "payoff" example) is fast but
    # real; it must never trip the guard.
    word_lists = [[_span(f"w{i}", i * 150, (i + 1) * 150) for i in range(11)]]
    assert _implausible_segment_indices(word_lists) == set()


def test_implausible_threshold_is_exclusive_at_exactly_8_wps() -> None:
    # 8 words in exactly 1000ms is exactly `MAX_PLAUSIBLE_WORDS_PER_SECOND` —
    # the check is a strict `>`, so a rate *equal* to the threshold is still
    # plausible (the threshold is the first flagged value, not the last
    # tolerated one).
    assert MAX_PLAUSIBLE_WORDS_PER_SECOND == 8.0
    word_lists = [[_span(f"w{i}", i * 125, (i + 1) * 125) for i in range(8)]]
    assert _implausible_segment_indices(word_lists) == set()


def test_implausible_flags_just_above_the_threshold() -> None:
    # The same 8 words one millisecond faster (999ms instead of 1000ms) is
    # 8.008 words/sec — just past the threshold, and now flagged.
    word_lists = [[_span("w", 0, 999)] + [_span(f"w{i}", 999, 999) for i in range(7)]]
    assert _implausible_segment_indices(word_lists) == {0}


def test_implausible_skips_empty_word_lists() -> None:
    # An empty segment is the pre-existing `_derive_timings_from_alignment`
    # total-failure trigger, a different failure category — this detector
    # must not also flag it (that would double-count one failure as two).
    word_lists = [[_span("hi", 0, 500)], []]
    assert _implausible_segment_indices(word_lists) == set()


def test_implausible_guards_zero_duration_without_raising() -> None:
    # A word span whose start equals its end (a zero-duration collapse) must
    # not raise ZeroDivisionError; it is automatically implausible (an
    # infinite implied rate).
    word_lists = [[_span("w", 100, 100)]]
    assert _implausible_segment_indices(word_lists) == {0}


def test_implausible_flags_only_the_segments_that_fail() -> None:
    word_lists = [
        [_span("ok1", 0, 500)],  # 2 wps, plausible
        [_span(f"bad{i}", i * 4, (i + 1) * 4) for i in range(11)],  # crushed
        [_span("ok2", 1000, 1500)],  # 2 wps, plausible
    ]
    assert _implausible_segment_indices(word_lists) == {1}


# --- Build-level per-segment plausibility guard (ADR 0066, issue #154) -----
#
# `MediaPipeline.build` end-to-end: proves the guard is surgical AND
# word-data-only — flagged segments lose exactly their karaoke words, no
# cue's boundary moves, and every plausible segment keeps its real derived
# boundary and words.


def test_build_clears_words_when_last_segment_is_implausible() -> None:
    """The load-bearing regression test for #154 / ADR 0066.

    Mirrors #154's own measured pathology: the LAST of three segments aligns
    to an 11-word span crushed into ~44ms (a ~250 words/sec implied rate),
    while the first two segments align normally (2.5 words/sec each). Before
    the guard, `MediaPipeline.build` attached the crushed 11-word span
    verbatim to the last cue — a `\\kf` sweep that finishes in 44ms and then
    sits frozen for the rest of the cue, #154's garbled-flash symptom. The
    guard clears exactly that word data and nothing else: every cue boundary
    (the flagged cue's included) stays what the derivation produced.
    """
    word_lists = [
        [_span(f"a{i}", i * 400, (i + 1) * 400) for i in range(5)],  # 0-2000, 2.5 wps
        [_span(f"b{i}", 2000 + i * 400, 2000 + (i + 1) * 400) for i in range(5)],  # 2000-4000
        [_span(f"c{i}", 4000 + i * 4, 4000 + (i + 1) * 4) for i in range(11)],  # 4000-4044!
    ]
    aligner = _FixedWordAligner(word_lists)
    # ms_per_char=200 gives this narration (30 chars) audio.duration_ms=6000 —
    # comfortably above the 4000ms the non-final boundaries need (ADR 0065's
    # own boundary check), so the fixture exercises the derive-then-guard
    # path rather than accidentally tripping the unrelated total-failure path.
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=200), FakeCompositionService(), word_aligner=aligner
    )
    segments = ["hook line", "build line", "loop line"]
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "ignored")), segments=segments))

    cues = plan.captions.cues
    assert len(cues) == 3

    # (a) The crushed segment's words are cleared — no karaoke sweep is ever
    # rendered from data known to be garbage; the cue degrades to the plain
    # cue-level fade (ADR 0059).
    assert cues[2].words == []

    # (b) Its boundary is untouched: exactly the derivation's own value —
    # (its real aligned start, audio.duration_ms) — because ADR 0065's
    # endpoint pinning already replaced the crushed raw end (4044) before
    # the guard ever ran. The guard changed no timing.
    assert (cues[2].start_ms, cues[2].end_ms) == (4000, plan.audio.duration_ms)

    # (c) The OTHER two segments keep their real aligned boundaries and words
    # completely unchanged — the key proof this is surgical, not a full
    # `_allocate_timings` fallback for the whole narration.
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 2000)
    assert (cues[1].start_ms, cues[1].end_ms) == (2000, 4000)
    assert [w.text for w in cues[0].words] == [f"a{i}" for i in range(5)]
    assert [w.text for w in cues[1].words] == [f"b{i}" for i in range(5)]
    # Not the whole-narration char-count fallback either.
    guessed = _allocate_timings(segments, plan.audio.duration_ms)
    assert [(c.start_ms, c.end_ms) for c in cues] != guessed


def test_plausibility_guard_never_alters_cue_boundaries() -> None:
    """Locked contract (PR #155's independent re-review, 2026-07-07).

    The plausibility guard affects ONLY karaoke word data: with the guard
    demonstrably firing (a cue rendered word-free), every cue boundary is
    byte-identical to what `_derive_timings_from_alignment` alone produces
    for the same alignment data — for #154's observed geometry (an
    implausible LAST segment) and for an implausible MIDDLE segment alike.
    The re-review proved any "anchor to plausible neighbors" correction is
    an identity operation on the derivation's contiguous, endpoint-pinned
    output, so this PR deliberately ships no boundary logic; the actual
    crushed-cue-window fix is #154's separate chunked per-beat alignment
    work.
    """
    crushed_last = [
        [_span(f"a{i}", i * 400, (i + 1) * 400) for i in range(5)],
        [_span(f"b{i}", 2000 + i * 400, 2000 + (i + 1) * 400) for i in range(5)],
        [_span(f"c{i}", 4000 + i * 4, 4000 + (i + 1) * 4) for i in range(11)],  # crushed
    ]
    crushed_middle = [
        [_span(f"a{i}", i * 400, (i + 1) * 400) for i in range(5)],
        [_span(f"b{i}", 2000 + i * 4, 2000 + (i + 1) * 4) for i in range(11)],  # crushed
        [_span(f"c{i}", 2500 + i * 400, 2500 + (i + 1) * 400) for i in range(5)],
    ]
    segments = ["hook line", "build line", "loop line"]
    for word_lists in (crushed_last, crushed_middle):
        pipeline = MediaPipeline(
            FakeTTSProvider(ms_per_char=200),
            FakeCompositionService(),
            word_aligner=_FixedWordAligner(word_lists),
        )
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "ignored")), segments=segments))
        # The guard fired: exactly one cue was stripped of its words...
        assert sum(1 for cue in plan.captions.cues if cue.words == []) == 1
        # ...and yet every boundary is byte-identical to the ADR 0065
        # derivation's own output for the same word lists.
        assert [(c.start_ms, c.end_ms) for c in plan.captions.cues] == (
            _derive_timings_from_alignment(word_lists, plan.audio.duration_ms)
        )


def test_build_clears_words_only_for_an_implausible_middle_segment() -> None:
    word_lists = [
        [_span(f"a{i}", i * 400, (i + 1) * 400) for i in range(5)],  # 0-2000, plausible
        [_span(f"b{i}", 2000 + i * 4, 2000 + (i + 1) * 4) for i in range(11)],  # crushed
        [_span(f"c{i}", 2500 + i * 400, 2500 + (i + 1) * 400) for i in range(5)],  # plausible
    ]
    aligner = _FixedWordAligner(word_lists)
    # ms_per_char=250 gives this narration (13 chars) audio.duration_ms=3250 —
    # comfortably above the 2500ms the non-final boundaries need.
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=250), FakeCompositionService(), word_aligner=aligner
    )
    segments = ["one", "two", "three"]
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "ignored")), segments=segments))

    cues = plan.captions.cues
    assert cues[1].words == []
    # The flagged cue's boundary is the derivation's own value — its real
    # aligned start, gap-bridged forward to the next segment's real start by
    # ADR 0065's rule 1 — because the guard left it alone.
    assert (cues[1].start_ms, cues[1].end_ms) == (2000, 2500)
    # Neighbors are untouched.
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 2000)
    assert [w.text for w in cues[0].words] == [f"a{i}" for i in range(5)]
    assert [w.text for w in cues[2].words] == [f"c{i}" for i in range(5)]


def test_build_clears_words_for_every_flagged_segment() -> None:
    # Two CONSECUTIVE implausible segments sandwiched between two valid ones:
    # each flagged segment loses its own words; the valid ones keep theirs;
    # and the boundaries — including the two flagged cues' — are exactly the
    # derivation's output, untouched by the guard.
    word_lists = [
        [_span(f"a{i}", i * 400, (i + 1) * 400) for i in range(5)],  # 0-2000, plausible
        [_span(f"b{i}", 2000 + i * 4, 2000 + (i + 1) * 4) for i in range(11)],  # crushed
        [_span(f"c{i}", 2044 + i * 4, 2044 + (i + 1) * 4) for i in range(11)],  # crushed
        [_span(f"d{i}", 2500 + i * 400, 2500 + (i + 1) * 400) for i in range(5)],  # plausible
    ]
    aligner = _FixedWordAligner(word_lists)
    # ms_per_char=300 gives this narration (11 chars) audio.duration_ms=3300 —
    # comfortably above the 2500ms the non-final boundaries need.
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=300), FakeCompositionService(), word_aligner=aligner
    )
    segments = ["s0", "s1", "s2", "s3"]
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "ignored")), segments=segments))

    cues = plan.captions.cues
    boundaries = [(c.start_ms, c.end_ms) for c in cues]
    assert boundaries == _derive_timings_from_alignment(word_lists, plan.audio.duration_ms)
    # Both crushed cues lost their words; both valid ones kept theirs.
    assert cues[1].words == []
    assert cues[2].words == []
    assert [w.text for w in cues[0].words] == [f"a{i}" for i in range(5)]
    assert [w.text for w in cues[3].words] == [f"d{i}" for i in range(5)]


def test_build_falls_back_fully_when_every_segment_is_implausible(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Pathological (no evidence this occurs in practice, per #154's own
    # observations): every segment fails plausibility, so there is no real
    # alignment left worth preserving. Must widen to the same whole-narration
    # `_allocate_timings` fallback ADR 0065 already uses for total alignment
    # failure — never derived boundaries that are garbage end to end.
    word_lists = [
        [_span(f"a{i}", i * 4, (i + 1) * 4) for i in range(11)],
        [_span(f"b{i}", 44 + i * 4, 44 + (i + 1) * 4) for i in range(11)],
    ]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    segments = ["one", "two"]
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "ignored")), segments=segments))

    expected = _allocate_timings(segments, plan.audio.duration_ms)
    actual = [(c.start_ms, c.end_ms) for c in plan.captions.cues]
    assert actual == expected
    assert all(cue.words == [] for cue in plan.captions.cues)
    assert any(
        "impossible speaking rate" in record.getMessage() and "every segment" in record.getMessage()
        for record in caplog.records
    )


def test_build_logs_no_plausibility_warning_for_realistic_alignment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A normal, entirely plausible alignment (well under 8 wps everywhere)
    # must not emit the ADR 0066 warning at all, and every cue keeps its real
    # derived boundary and words exactly as ADR 0065 already produces.
    word_lists = [[_span("one", 0, 900)], [_span("two", 900, 1050)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=150), FakeCompositionService(), word_aligner=aligner
    )
    with caplog.at_level(logging.WARNING, logger="app.media.pipeline"):
        plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    assert not any("impossible speaking rate" in record.getMessage() for record in caplog.records)
    cues = plan.captions.cues
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 900)
    assert (cues[1].start_ms, cues[1].end_ms) == (900, 1050)
    assert [w.text for w in cues[0].words] == ["one"]
    assert [w.text for w in cues[1].words] == ["two"]
