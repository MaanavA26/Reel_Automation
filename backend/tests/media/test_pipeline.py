"""Tests for the MediaPipeline creator-packet → media handoff (ADR 0025).

Fully hermetic: the `FakeTTSProvider` and `FakeCompositionService` seams plus the
real `DeterministicSubtitleService` (pure, no fake needed). No network, no ffmpeg.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest

from app.media.composition.base import FakeCompositionService
from app.media.pipeline import (
    MediaPipeline,
    MediaPipelineError,
    MediaPlan,
    _allocate_timings,
    _split_into_beats,
)
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
