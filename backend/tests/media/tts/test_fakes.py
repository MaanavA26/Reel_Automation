"""Tests for the hermetic FakeTTSProvider."""

from __future__ import annotations

import asyncio

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import FakeTTSProvider, TTSProvider


def test_satisfies_protocol() -> None:
    assert isinstance(FakeTTSProvider(), TTSProvider)


def test_synthesize_returns_descriptor_and_records_call() -> None:
    provider = FakeTTSProvider(ms_per_char=10)
    speech = asyncio.run(provider.synthesize(text="hello", voice="narrator"))
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.voice == "narrator"
    assert speech.duration_ms == 50  # 5 chars * 10ms
    assert speech.produced_via == "tts:fake"
    assert provider.calls[0].text == "hello"
    assert provider.calls[0].voice == "narrator"


def test_distinct_uris_per_call() -> None:
    provider = FakeTTSProvider()
    a = asyncio.run(provider.synthesize(text="a", voice="v"))
    b = asyncio.run(provider.synthesize(text="b", voice="v"))
    assert a.audio_uri != b.audio_uri
