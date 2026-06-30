"""Tests for the pure SRT/VTT/ASS formatters and the deterministic subtitle service."""

from __future__ import annotations

import pytest

from app.media.schemas import Caption, CaptionStyle, CaptionTrack
from app.media.subtitles.base import (
    _ASS_EVENT_FORMAT,
    _ASS_STYLE_FORMAT,
    DeterministicSubtitleService,
    SubtitleService,
    _ass_colour,
    _format_ass_timestamp,
    format_ass,
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


# --- format_ass (styled burned-in captions, ADR 0059) ----------------------


def _ass(track: CaptionTrack, *, width: int = 1080, height: int = 1920) -> str:
    return format_ass(track, style=CaptionStyle(), width=width, height=height)


def test_ass_has_three_sections_and_play_resolution() -> None:
    out = _ass(_track(Caption(start_ms=0, end_ms=1000, text="hi")), width=1080, height=1920)
    assert "[Script Info]" in out
    assert "[V4+ Styles]" in out  # exact header (not [V4 Styles])
    assert "[Events]" in out
    assert "ScriptType: v4.00+" in out
    assert "PlayResX: 1080" in out
    assert "PlayResY: 1920" in out
    assert "ScaledBorderAndShadow: yes" in out
    assert "WrapStyle:" in out


def test_ass_style_and_dialogue_field_counts_match_their_formats() -> None:
    # The closest hermetic proxy for "libass will accept this": the Style row and
    # each Dialogue row must have the same field count as their Format: lines.
    out = _ass(
        _track(
            Caption(start_ms=0, end_ms=1000, text="one"),
            Caption(start_ms=1000, end_ms=2000, text="two"),
        )
    )
    style_fields = len(_ASS_STYLE_FORMAT.split(","))
    assert style_fields == 23  # canonical V4+ field count
    event_fields = len(_ASS_EVENT_FORMAT.split(","))

    style_rows = [ln for ln in out.splitlines() if ln.startswith("Style:")]
    assert len(style_rows) == 1
    # "Style: a,b,..." -> the value list after the prefix.
    assert len(style_rows[0][len("Style:") :].split(",")) == style_fields

    dialogue_rows = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_rows) == 2
    for row in dialogue_rows:
        # Text is the last field and may itself contain commas; split with a cap
        # so commas inside the override/text don't inflate the count.
        assert len(row[len("Dialogue:") :].split(",", event_fields - 1)) == event_fields


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "0:00:00.00"),
        (4, "0:00:00.00"),  # <10ms truncates to 0 cc
        (1995, "0:00:01.99"),  # truncate boundary: NOT rounded up to .00/+1s
        (995, "0:00:00.99"),  # would be cc=100 if rounded; truncation keeps 99
        ((1 * 3600 + 2 * 60 + 3) * 1000 + 450, "1:02:03.45"),
    ],
)
def test_ass_timestamp_truncates_to_centiseconds(ms: int, expected: str) -> None:
    assert _format_ass_timestamp(ms) == expected


def test_ass_timestamp_rejects_negative() -> None:
    with pytest.raises(ValueError):
        _format_ass_timestamp(-1)


def test_ass_margins_are_at_least_ten_percent_of_width() -> None:
    out = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text="hi")),
        style=CaptionStyle(),  # default margin_fraction = 0.10
        width=1080,
        height=1920,
    )
    style_row = next(ln for ln in out.splitlines() if ln.startswith("Style:"))
    fields = style_row[len("Style:") :].split(",")
    names = [f.strip() for f in _ASS_STYLE_FORMAT.split(",")]
    margin_l = int(fields[names.index("MarginL")])
    margin_r = int(fields[names.index("MarginR")])
    assert margin_l == round(0.10 * 1080) == 108
    assert margin_r == 108
    # Alignment 2 = bottom-centre.
    assert int(fields[names.index("Alignment")]) == 2


def test_ass_per_cue_fade_uses_configured_ms() -> None:
    style = CaptionStyle(fade_in_ms=150, fade_out_ms=300)
    out = format_ass(
        _track(
            Caption(start_ms=0, end_ms=1000, text="one"),
            Caption(start_ms=1000, end_ms=2000, text="two"),
        ),
        style=style,
        width=1080,
        height=1920,
    )
    dialogue_rows = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_rows) == 2
    for row in dialogue_rows:
        assert r"{\fad(150,300)}" in row


def test_ass_colour_conversion_is_inverted_alpha_bgr() -> None:
    # ASS is &HAABBGGRR with alpha 00 = opaque. Use an asymmetric triple so a
    # partial B/G/R confusion is caught (white/red would not catch it).
    assert _ass_colour("#123456") == "&H00563412"
    assert _ass_colour("#FFFFFF") == "&H00FFFFFF"
    assert _ass_colour("#FF0000") == "&H000000FF"  # red -> R in the low byte


def test_ass_style_row_uses_converted_colours() -> None:
    out = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text="hi")),
        style=CaptionStyle(primary_colour="#123456", outline_colour="#000000"),
        width=1080,
        height=1920,
    )
    style_row = next(ln for ln in out.splitlines() if ln.startswith("Style:"))
    assert "&H00563412" in style_row  # primary converted
    assert "&H00000000" in style_row  # outline converted


def test_ass_colour_rejects_bad_hex() -> None:
    with pytest.raises(ValueError):
        _ass_colour("#12345")  # too short
    with pytest.raises(ValueError):
        _ass_colour("#12345Z")  # non-hex


def test_ass_escapes_override_characters_in_cue_text() -> None:
    # A cue with raw ASS override chars must not inject a real override block.
    out = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text=r"a{b}c\d \N e")),
        style=CaptionStyle(),
        width=1080,
        height=1920,
    )
    dialogue = next(ln for ln in out.splitlines() if ln.startswith("Dialogue:"))
    # Everything after the legitimate fade override is the text portion.
    fade = r"{\fad(120,120)}"
    text_part = dialogue.split(fade, 1)[1]
    assert "{" not in text_part
    assert "}" not in text_part
    assert "\\" not in text_part  # no raw backslash -> no \N / \h injection


def test_ass_rejects_nonpositive_dimensions() -> None:
    with pytest.raises(ValueError):
        format_ass(_track(), style=CaptionStyle(), width=0, height=1920)


def test_ass_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        format_ass(
            _track(Caption(start_ms=2000, end_ms=1000, text="bad")),
            style=CaptionStyle(),
            width=1080,
            height=1920,
        )
