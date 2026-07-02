"""Tests for Media layer artifact DTOs (id prefixes, strictness, provenance)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.media.schemas import (
    DEFAULT_CAPTION_STYLE,
    Caption,
    CaptionStyle,
    CaptionTrack,
    RenderedVideo,
    SynthesizedSpeech,
    WordSpan,
)


def test_ids_are_prefixed_and_unique() -> None:
    a = SynthesizedSpeech(audio_uri="x://a", duration_ms=0, voice="v", produced_via="tts:fake")
    b = SynthesizedSpeech(audio_uri="x://b", duration_ms=0, voice="v", produced_via="tts:fake")
    assert a.id.startswith("aud_") and b.id.startswith("aud_")
    assert a.id != b.id
    assert CaptionTrack(produced_via="subtitles:deterministic").id.startswith("sub_")
    vid = RenderedVideo(
        video_uri="x://v", duration_ms=0, width=1, height=1, produced_via="composition:fake"
    )
    assert vid.id.startswith("vid_")


def test_strict_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        SynthesizedSpeech(
            audio_uri="x://a",
            duration_ms=0,
            voice="v",
            produced_via="tts:fake",
            bogus="nope",  # type: ignore[call-arg]
        )


def test_produced_via_is_required() -> None:
    with pytest.raises(ValidationError):
        RenderedVideo(video_uri="x://v", duration_ms=0, width=1, height=1)  # type: ignore[call-arg]


def test_caption_carries_no_provenance() -> None:
    # A `Caption` is a sub-cue of a `CaptionTrack`; the track owns `produced_via`.
    cue = Caption(start_ms=0, end_ms=1000, text="hello")
    assert not hasattr(cue, "produced_via")


# --- CaptionStyle validation + immutability (ADR 0059, #132 review) ---------


@pytest.mark.parametrize(
    "bad_font",
    [
        "Comic, Sans",  # comma corrupts the comma-delimited ASS Style: row
        "Arial\nBlack",  # newline breaks the single-line row
        "Arial\rBlack",  # carriage return
        "Arial\x00",  # control character
    ],
)
def test_caption_style_rejects_unsafe_font_name(bad_font: str) -> None:
    with pytest.raises(ValidationError):
        CaptionStyle(font_name=bad_font)


def test_caption_style_accepts_normal_font_name() -> None:
    assert CaptionStyle(font_name="Montserrat SemiBold").font_name == "Montserrat SemiBold"


@pytest.mark.parametrize("field", ["primary_colour", "secondary_colour", "outline_colour"])
@pytest.mark.parametrize("bad_colour", ["123456", "##123456", "#12345", "#GGGGGG"])
def test_caption_style_rejects_bad_colour(field: str, bad_colour: str) -> None:
    with pytest.raises(ValidationError):
        CaptionStyle(**{field: bad_colour})


def test_caption_style_accepts_valid_colour() -> None:
    style = CaptionStyle(primary_colour="#12ab34", outline_colour="#ABCDEF")
    assert style.primary_colour == "#12ab34"
    assert style.outline_colour == "#ABCDEF"


def test_caption_style_is_frozen() -> None:
    # Frozen so the shared DEFAULT_CAPTION_STYLE can't be mutated in place.
    style = CaptionStyle()
    with pytest.raises(ValidationError):
        style.font_name = "Other"  # type: ignore[misc]


def test_default_caption_style_is_a_caption_style() -> None:
    assert isinstance(DEFAULT_CAPTION_STYLE, CaptionStyle)
    with pytest.raises(ValidationError):
        DEFAULT_CAPTION_STYLE.font_size = 10  # type: ignore[misc]


def test_caption_style_secondary_colour_default() -> None:
    # The karaoke pre-highlight fill (ADR 0062): a dimmed default distinct from
    # the primary, so a default-styled karaoke sweep is actually visible.
    assert CaptionStyle().secondary_colour == "#808080"
    assert CaptionStyle().secondary_colour != CaptionStyle().primary_colour


# --- WordSpan + Caption.words (word-level karaoke carrier, ADR 0062) ---------


def test_word_span_is_strict_and_nonnegative() -> None:
    span = WordSpan(text="hello", start_ms=10, end_ms=400)
    assert (span.text, span.start_ms, span.end_ms) == ("hello", 10, 400)
    with pytest.raises(ValidationError):
        WordSpan(text="x", start_ms=-1, end_ms=0)
    with pytest.raises(ValidationError):
        WordSpan(text="x", start_ms=0, end_ms=0, bogus="nope")  # type: ignore[call-arg]


def test_caption_words_default_empty_keeps_old_constructions_valid() -> None:
    # The additive-carrier lock: every pre-ADR-0062 construction stays valid
    # and word-free.
    cue = Caption(start_ms=0, end_ms=1000, text="hello")
    assert cue.words == []


def test_caption_carries_word_spans() -> None:
    cue = Caption(
        start_ms=0,
        end_ms=1000,
        text="hello world",
        words=[
            WordSpan(text="hello", start_ms=0, end_ms=400),
            WordSpan(text="world", start_ms=450, end_ms=1000),
        ],
    )
    assert [w.text for w in cue.words] == ["hello", "world"]


def test_caption_track_round_trips_word_spans() -> None:
    # Word timings survive serialization: a persisted MediaPlan's captions keep
    # their karaoke carrier.
    track = CaptionTrack(
        cues=[
            Caption(
                start_ms=0,
                end_ms=1000,
                text="hi",
                words=[WordSpan(text="hi", start_ms=0, end_ms=900)],
            )
        ],
        produced_via="subtitles:deterministic",
    )
    restored = CaptionTrack.model_validate_json(track.model_dump_json())
    assert restored.cues[0].words == track.cues[0].words
