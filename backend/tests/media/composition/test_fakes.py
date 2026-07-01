"""Tests for the hermetic FakeCompositionService."""

from __future__ import annotations

import asyncio

from app.media.composition.base import CompositionService, FakeCompositionService
from app.media.schemas import (
    DEFAULT_CAPTION_STYLE,
    CaptionStyle,
    CaptionTrack,
    RenderedVideo,
    SynthesizedSpeech,
)


def _audio() -> SynthesizedSpeech:
    return SynthesizedSpeech(
        audio_uri="fake://a.wav", duration_ms=4200, voice="narrator", produced_via="tts:fake"
    )


def _captions() -> CaptionTrack:
    return CaptionTrack(produced_via="subtitles:deterministic")


def test_satisfies_protocol() -> None:
    assert isinstance(FakeCompositionService(), CompositionService)


def test_render_returns_descriptor_and_records_call() -> None:
    service = FakeCompositionService()
    audio = _audio()
    video = asyncio.run(
        service.render(audio=audio, captions=_captions(), visual_uris=["fake://bg.png"])
    )
    assert isinstance(video, RenderedVideo)
    assert video.duration_ms == audio.duration_ms  # video matches narration length
    assert (video.width, video.height) == (1080, 1920)  # vertical default
    assert video.produced_via == "composition:fake"
    assert service.calls[0].audio_id == audio.id
    assert service.calls[0].visual_uris == ["fake://bg.png"]


def test_render_echoes_requested_dimensions() -> None:
    service = FakeCompositionService()
    video = asyncio.run(
        service.render(audio=_audio(), captions=_captions(), visual_uris=[], width=720, height=1280)
    )
    assert (video.width, video.height) == (720, 1280)


def test_render_records_default_caption_style() -> None:
    # No explicit style -> the shared module-level default is captured.
    service = FakeCompositionService()
    asyncio.run(service.render(audio=_audio(), captions=_captions(), visual_uris=[]))
    assert service.calls[0].caption_style is DEFAULT_CAPTION_STYLE


def test_render_records_custom_caption_style() -> None:
    # Style propagation is assertable without real ffmpeg (ADR 0059, #132 review).
    service = FakeCompositionService()
    style = CaptionStyle(font_name="Montserrat", font_size=96, primary_colour="#123456")
    asyncio.run(
        service.render(audio=_audio(), captions=_captions(), visual_uris=[], caption_style=style)
    )
    assert service.calls[0].caption_style == style
