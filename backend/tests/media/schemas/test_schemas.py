"""Tests for Media layer artifact DTOs (id prefixes, strictness, provenance)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.media.schemas import Caption, CaptionTrack, RenderedVideo, SynthesizedSpeech


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
