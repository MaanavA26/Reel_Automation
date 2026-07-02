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


@pytest.mark.parametrize("field", ["primary_colour", "outline_colour"])
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
