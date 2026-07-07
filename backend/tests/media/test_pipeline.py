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
    MediaPipeline,
    MediaPipelineError,
    MediaPlan,
    _allocate_timings,
    _derive_timings_from_alignment,
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
    # ms_per_word=50 (not FakeWordAligner's own default of 300, and not 100)
    # is deliberate: it keeps this fake aligner's running clock inside the
    # FakeTTSProvider's declared audio.duration_ms (130ms for this narration
    # at ms_per_char=10), which ADR 0065's boundary validation now requires
    # for `_derive_timings_from_alignment` to succeed instead of falling back
    # (a mismatched fake clock — e.g. ms_per_word=100 — legitimately exceeds
    # total_ms mid-track and triggers the degrade path, exactly as it should
    # for a real aligner whose measurements didn't fit the real audio either).
    aligner = FakeWordAligner(ms_per_word=50)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one two\nthree"))))

    assert [len(cue.words) for cue in plan.captions.cues] == [2, 1]
    assert [w.text for w in plan.captions.cues[0].words] == ["one", "two"]
    # The aligner was asked exactly once, for the narration audio + the beats.
    assert len(aligner.calls) == 1
    assert aligner.calls[0].audio_path == plan.audio.audio_uri
    assert aligner.calls[0].segments == ["one two", "three"]
    # ADR 0065: cue boundaries come from the SAME alignment, not a char-count
    # guess — one(0-50) two(50-100) -> cue 0 spans [0, 100); three(100-150),
    # last cue's end pinned to the true audio duration (130), not 150.
    assert (plan.captions.cues[0].start_ms, plan.captions.cues[0].end_ms) == (0, 100)
    assert (plan.captions.cues[1].start_ms, plan.captions.cues[1].end_ms) == (100, 130)


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
    real speech share diverges sharply from its character-count share).
    """
    word_lists = [[_span("one", 0, 60)], [_span("two", 60, 70)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "one\ntwo"))))

    # The pre-fix guess, spelled out so the contrast with the assertions below
    # is explicit (this is exactly what pre-fix code would have produced).
    guessed = _allocate_timings(["one", "two"], plan.audio.duration_ms)
    assert guessed == [(0, 35), (35, 70)]

    cues = plan.captions.cues
    assert (cues[0].start_ms, cues[0].end_ms) == (0, 60)
    assert (cues[1].start_ms, cues[1].end_ms) == (60, 70)
    assert (cues[0].start_ms, cues[0].end_ms) != guessed[0]
    assert (cues[1].start_ms, cues[1].end_ms) != guessed[1]
    # The karaoke carrier comes from the identical source as the boundaries.
    assert [w.text for w in cues[0].words] == ["one"]
    assert [w.text for w in cues[1].words] == ["two"]


def test_build_pins_endpoints_even_when_alignment_falls_short() -> None:
    # The aligner's first word starts at 10ms (not 0) and its last word ends
    # at 95ms while the real audio runs to 110ms (trailing silence) — the
    # derived first/last cue must still hit the exact endpoints.
    word_lists = [[_span("hello", 10, 50)], [_span("world", 50, 95)]]
    aligner = _FixedWordAligner(word_lists)
    pipeline = MediaPipeline(
        FakeTTSProvider(ms_per_char=10), FakeCompositionService(), word_aligner=aligner
    )
    plan = asyncio.run(pipeline.build(_packet(_narrative("Arc", "hello\nworld"))))

    assert plan.captions.cues[0].start_ms == 0
    assert plan.captions.cues[-1].end_ms == plan.audio.duration_ms == 110


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
