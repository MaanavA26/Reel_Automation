"""Tests for the pure SRT/VTT formatters and the deterministic subtitle service."""

from __future__ import annotations

import pytest

from app.media.schemas import Caption, CaptionTrack
from app.media.subtitles.base import (
    DeterministicSubtitleService,
    SubtitleService,
    format_srt,
    format_vtt,
)


def _track(*cues: Caption) -> CaptionTrack:
    return CaptionTrack(cues=list(cues), produced_via="subtitles:deterministic")


def test_srt_vs_vtt_discriminators() -> None:
    # The same cue rendered both ways: SRT uses a comma before ms and an index
    # line; VTT uses a period and a WEBVTT header.
    track = _track(Caption(start_ms=1500, end_ms=3250, text="hi"))
    srt = format_srt(track)
    vtt = format_vtt(track)

    assert "00:00:01,500 --> 00:00:03,250" in srt
    assert srt.startswith("1\n")  # 1-based index line
    assert "WEBVTT" not in srt

    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.500 --> 00:00:03.250" in vtt
    assert "," not in vtt.split("\n")[2]  # cue timing line uses '.'


def test_timestamp_hours_rollover_and_padding() -> None:
    # 1h 02m 03s 004ms -> zero-padded H:M:S, 3-digit ms.
    ms = (1 * 3600 + 2 * 60 + 3) * 1000 + 4
    srt = format_srt(_track(Caption(start_ms=ms, end_ms=ms, text="x")))
    assert "01:02:03,004 --> 01:02:03,004" in srt


def test_zero_timestamp() -> None:
    srt = format_srt(_track(Caption(start_ms=0, end_ms=0, text="x")))
    assert "00:00:00,000 --> 00:00:00,000" in srt


def test_multiple_cues_indexed_and_blank_separated() -> None:
    srt = format_srt(
        _track(
            Caption(start_ms=0, end_ms=1000, text="one"),
            Caption(start_ms=1000, end_ms=2000, text="two"),
        )
    )
    assert srt.startswith("1\n")
    assert "\n2\n" in srt


def test_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        format_srt(_track(Caption(start_ms=2000, end_ms=1000, text="bad")))


def test_deterministic_service_builds_track() -> None:
    service = DeterministicSubtitleService()
    assert isinstance(service, SubtitleService)
    track = service.build_track(
        segments=["one", "two"],
        timings=[(0, 1000), (1000, 2000)],
    )
    assert track.produced_via == "subtitles:deterministic"
    assert [c.text for c in track.cues] == ["one", "two"]
    assert track.cues[1].start_ms == 1000


def test_service_rejects_mismatched_lengths() -> None:
    service = DeterministicSubtitleService()
    with pytest.raises(ValueError):
        service.build_track(segments=["only one"], timings=[(0, 1), (1, 2)])
