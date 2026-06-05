"""Tests for the HTTP TTS provider adapter.

Fully offline: an injected ``httpx.MockTransport`` stands in for the network and
a capturing in-memory ``sink`` stands in for storage, so request building, the
bytes → ``SynthesizedSpeech`` mapping, and the duration-header contract are all
verified without a live call. The live call itself is covered by a separate
``@pytest.mark.integration`` smoke test (skipped without creds). Mirrors
``tests/services/llm/test_openai_compatible.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.http_tts import (
    DURATION_HEADER,
    HttpTtsError,
    HttpTtsProvider,
)


class _CapturingSink:
    """An in-memory ``AudioSink`` that records bytes and returns a stable URI."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.received.append(audio)
        return f"mem://audio/{len(self.received)}.bin"


def _provider(handler: Any, sink: Any | None = None) -> HttpTtsProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTtsProvider(
        base_url="https://x/v1",
        api_key="k",
        sink=sink or _CapturingSink(),
        client=client,
    )


def _audio_resp(audio: bytes, *, duration_ms: int | None = 1234) -> httpx.Response:
    headers = {} if duration_ms is None else {DURATION_HEADER: str(duration_ms)}
    return httpx.Response(200, content=audio, headers=headers)


def _synthesize(
    provider: HttpTtsProvider, *, text: str = "hello", voice: str = "narrator"
) -> SynthesizedSpeech:
    return asyncio.run(provider.synthesize(text=text, voice=voice))


def test_satisfies_protocol() -> None:
    assert isinstance(_provider(lambda r: _audio_resp(b"")), TTSProvider)


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}
    sink = _CapturingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return _audio_resp(b"RIFFfake-audio", duration_ms=4200)

    speech = _synthesize(_provider(handler, sink), text="hi there", voice="anchor")

    # Request shape.
    assert seen["url"] == "https://x/v1/synthesize"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {"text": "hi there", "voice": "anchor"}

    # Response mapping into the descriptor.
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms == 4200
    assert speech.voice == "anchor"
    assert speech.produced_via == "tts:http"
    assert speech.audio_uri == "mem://audio/1.bin"

    # Raw audio bytes were handed to the storage sink (not embedded in the DTO).
    assert sink.received == [b"RIFFfake-audio"]


def test_trailing_slash_base_url_normalized() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _audio_resp(b"x")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpTtsProvider(
        base_url="https://x/v1/", api_key="k", sink=_CapturingSink(), client=client
    )
    _synthesize(provider)
    assert seen["url"] == "https://x/v1/synthesize"


def test_http_error_surfaces() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _synthesize(_provider(lambda r: httpx.Response(503, json={"error": "down"})))


def test_missing_duration_header_raises() -> None:
    with pytest.raises(HttpTtsError, match=DURATION_HEADER):
        _synthesize(_provider(lambda r: _audio_resp(b"x", duration_ms=None)))


def test_non_integer_duration_header_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={DURATION_HEADER: "soon"})

    with pytest.raises(HttpTtsError, match="not an integer"):
        _synthesize(_provider(handler))


def test_negative_duration_header_raises() -> None:
    with pytest.raises(HttpTtsError, match="negative"):
        _synthesize(_provider(lambda r: _audio_resp(b"x", duration_ms=-5)))


def test_missing_base_url_raises() -> None:
    with pytest.raises(HttpTtsError):
        HttpTtsProvider(base_url="", api_key="k", sink=_CapturingSink())
