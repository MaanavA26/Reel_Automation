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
    _split_into_beats,
)
from app.media.schemas import WordSpan
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
    aligner = FakeWordAligner(ms_per_word=100)
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
