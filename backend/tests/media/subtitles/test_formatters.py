"""Tests for the pure SRT/VTT/ASS formatters and the deterministic subtitle service."""

from __future__ import annotations

import re

import pytest

from app.media.schemas import Caption, CaptionStyle, CaptionTrack, WordSpan
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


def test_ass_style_row_carries_font_name_and_size() -> None:
    # A non-default font name + size must land in the Style: row's Fontname /
    # Fontsize fields (style propagation, #132 review).
    out = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text="hi")),
        style=CaptionStyle(font_name="Montserrat", font_size=96),
        width=1080,
        height=1920,
    )
    style_row = next(ln for ln in out.splitlines() if ln.startswith("Style:"))
    fields = [f.strip() for f in style_row[len("Style:") :].split(",")]
    names = [f.strip() for f in _ASS_STYLE_FORMAT.split(",")]
    assert fields[names.index("Fontname")] == "Montserrat"
    assert fields[names.index("Fontsize")] == "96"


def test_ass_colour_accepts_valid_rrggbb() -> None:
    # A valid `#RRGGBB` passes and converts (regression alongside the tightened
    # rejection cases below).
    assert _ass_colour("#123456") == "&H00563412"


@pytest.mark.parametrize(
    "bad",
    [
        "123456",  # no leading '#' (lstrip('#') would have wrongly accepted this)
        "##123456",  # double '#' (lstrip('#') would have wrongly accepted this)
        "#12345",  # too short (5 hex digits)
        "#GGGGGG",  # right length, non-hex digits
        "#12345Z",  # non-hex
        "#1234567",  # too long
    ],
)
def test_ass_colour_rejects_bad_hex(bad: str) -> None:
    with pytest.raises(ValueError, match="colour must be"):
        _ass_colour(bad)


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


# --- format_ass word-level karaoke (ADR 0062) --------------------------------


def _dialogue_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]


def _karaoke_cue() -> Caption:
    # Cue span 1000-3000ms (200cs). Words leave a 200ms leading offset, a
    # 50ms inter-word gap, a zero gap, and a 5ms tail — chosen so every
    # emission rule (spacer, omission, truncation) shows up in one line.
    return Caption(
        start_ms=1000,
        end_ms=3000,
        text="Hello brave world",
        words=[
            WordSpan(text="Hello", start_ms=1200, end_ms=1650),
            WordSpan(text="brave", start_ms=1700, end_ms=2400),
            WordSpan(text="world", start_ms=2400, end_ms=2995),
        ],
    )


def test_ass_karaoke_emits_exact_kf_centisecond_tags() -> None:
    # The rounding lock: cue-relative boundaries are TRUNCATED to centiseconds
    # (// 10, the ADR 0059 decision) and durations are boundary differences.
    # Leading offset 200ms -> {\kf20} empty spacer; Hello 450ms -> 45; gap
    # 50ms -> 5; brave 700ms -> 70; zero gap omitted; world 595ms -> 59.
    out = _ass(_track(_karaoke_cue()))
    (dialogue,) = _dialogue_lines(out)
    expected = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Brand,0,0,0,,"
        r"{\fad(120,120)}{\kf20}{\kf45}Hello {\kf5}{\kf70}brave {\kf59}world"
    )
    assert dialogue == expected


def test_ass_karaoke_durations_truncate_not_round() -> None:
    # 995ms of word would be cc=100 if rounded; truncation keeps 99 (the same
    # boundary case _format_ass_timestamp locks).
    cue = Caption(
        start_ms=0, end_ms=1000, text="hi", words=[WordSpan(text="hi", start_ms=5, end_ms=995)]
    )
    (dialogue,) = _dialogue_lines(_ass(_track(cue)))
    # 5ms leading offset truncates to 0cs -> spacer omitted entirely.
    assert dialogue.endswith(r"{\fad(120,120)}{\kf99}hi")


def test_ass_karaoke_total_never_exceeds_cue_span() -> None:
    out = _ass(_track(_karaoke_cue()))
    (dialogue,) = _dialogue_lines(out)
    durations = [int(d) for d in re.findall(r"\\kf(\d+)", dialogue)]
    assert sum(durations) <= (3000 - 1000) // 10


def test_ass_karaoke_keeps_cue_fade() -> None:
    # Locked composition decision (ADR 0062): \fad animates line alpha, \kf
    # animates per-syllable fill — independent channels, so the fade stays on
    # karaoke lines and mixed tracks keep one uniform entrance/exit.
    (dialogue,) = _dialogue_lines(_ass(_track(_karaoke_cue())))
    assert r"{\fad(120,120)}{\kf" in dialogue


def test_ass_karaoke_escapes_word_text() -> None:
    cue = Caption(
        start_ms=0,
        end_ms=1000,
        text="raw",
        words=[WordSpan(text=r"a{b}\c", start_ms=0, end_ms=500)],
    )
    (dialogue,) = _dialogue_lines(_ass(_track(cue)))
    fade = r"{\fad(120,120)}"
    text_part = dialogue.split(fade, 1)[1]
    # The only braces/backslashes left are the karaoke override tags themselves.
    assert re.sub(r"\{\\kf\d+\}", "", text_part) == "a｛b｝/c"  # noqa: RUF001 (safe lookalikes)


def test_ass_karaoke_clamps_overhanging_words_into_cue_span() -> None:
    # Aligner word times and allocated cue boundaries are independent
    # estimates: a word may overhang the cue at either seam. Overhang clamps
    # (renders degrade, never fail): the early word starts at the cue start,
    # the late word is cut at the cue end, and the total still fits.
    cue = Caption(
        start_ms=1000,
        end_ms=2000,
        text="pre post",
        words=[
            WordSpan(text="pre", start_ms=900, end_ms=1100),
            WordSpan(text="post", start_ms=1900, end_ms=2300),
        ],
    )
    (dialogue,) = _dialogue_lines(_ass(_track(cue)))
    assert dialogue.endswith(r"{\fad(120,120)}{\kf10}pre {\kf80}{\kf10}post")


def test_ass_karaoke_inverted_word_span_raises() -> None:
    cue = Caption(
        start_ms=0,
        end_ms=1000,
        text="bad",
        words=[WordSpan(text="bad", start_ms=500, end_ms=400)],
    )
    with pytest.raises(ValueError, match="end_ms"):
        _ass(_track(cue))


def test_ass_karaoke_words_replace_cue_text() -> None:
    # When a cue carries word spans, they ARE the rendered text; the cue-level
    # text is not re-emitted alongside them.
    cue = Caption(
        start_ms=0,
        end_ms=1000,
        text="SHOULD NOT APPEAR",
        words=[WordSpan(text="only", start_ms=0, end_ms=1000)],
    )
    (dialogue,) = _dialogue_lines(_ass(_track(cue)))
    assert "SHOULD NOT APPEAR" not in dialogue
    assert dialogue.endswith(r"{\kf100}only")


def test_ass_no_words_track_is_byte_identical_to_cue_fade_format() -> None:
    # The graceful-degrade lock: a track with no word timings anywhere renders
    # EXACTLY the ADR 0059 cue-fade document — pinned as a golden so any drift
    # in the degraded path (including the SecondaryColour slot) fails loudly.
    out = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text="hi")),
        style=CaptionStyle(),
        width=1080,
        height=1920,
    )
    golden = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        f"Format: {_ASS_STYLE_FORMAT}\n"
        "Style: Brand,Arial,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,2,108,108,115,1\n"
        "\n"
        "[Events]\n"
        f"Format: {_ASS_EVENT_FORMAT}\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Brand,0,0,0,,{\\fad(120,120)}hi\n"
    )
    assert out == golden


def test_ass_mixed_track_degrades_per_cue() -> None:
    out = _ass(
        _track(
            _karaoke_cue(),
            Caption(start_ms=3000, end_ms=4000, text="plain tail"),
        )
    )
    first, second = _dialogue_lines(out)
    assert r"\kf" in first
    assert r"\kf" not in second
    assert second.endswith(r"{\fad(120,120)}plain tail")


def test_ass_secondary_colour_used_only_when_track_has_words() -> None:
    style = CaptionStyle(secondary_colour="#123456")
    names = [f.strip() for f in _ASS_STYLE_FORMAT.split(",")]

    def secondary_field(out: str) -> str:
        style_row = next(ln for ln in out.splitlines() if ln.startswith("Style:"))
        fields = style_row[len("Style:") :].split(",")
        return fields[names.index("SecondaryColour")].strip()

    with_words = format_ass(_track(_karaoke_cue()), style=style, width=1080, height=1920)
    assert secondary_field(with_words) == "&H00563412"  # karaoke pre-highlight fill

    without_words = format_ass(
        _track(Caption(start_ms=0, end_ms=1000, text="hi")),
        style=style,
        width=1080,
        height=1920,
    )
    assert secondary_field(without_words) == "&H00FFFFFF"  # == primary, byte-stable


def test_srt_ignores_word_timings() -> None:
    plain = _track(Caption(start_ms=0, end_ms=1000, text="hi"))
    with_words = _track(
        Caption(
            start_ms=0,
            end_ms=1000,
            text="hi",
            words=[WordSpan(text="hi", start_ms=0, end_ms=1000)],
        )
    )
    assert format_srt(plain) == format_srt(with_words)
