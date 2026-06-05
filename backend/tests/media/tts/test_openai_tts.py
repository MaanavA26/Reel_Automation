"""Tests for the OpenAI ``/audio/speech`` TTS provider adapter.

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, a
tmp-file ``sink`` stands in for storage, and the ``ffprobe`` subprocess seam is
either monkeypatched or driven via pure helpers — so request building, the
bytes → ``SynthesizedSpeech`` mapping, and the duration-probe contract are all
verified without a live call or the ``ffprobe`` binary. The live call itself is
covered by a separate ``@pytest.mark.integration`` smoke test. Mirrors
``tests/media/tts/test_http_tts.py`` and ``tests/media/composition`` argv tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.media.composition.ffmpeg import CompositionError
from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.openai_tts import (
    OpenAiTtsError,
    OpenAiTtsProvider,
    build_ffprobe_args,
    parse_ffprobe_duration_ms,
)


class _FileSink:
    """An ``AudioSink`` that writes bytes to a tmp dir and returns a file:// URI."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.received: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.received.append(audio)
        out = self._root / f"audio_{len(self.received)}.bin"
        out.write_bytes(audio)
        return out.as_uri()


def _audio_resp(audio: bytes = b"ID3fake-mp3") -> httpx.Response:
    return httpx.Response(200, content=audio)


def _provider(
    handler: Any,
    *,
    sink: Any,
    probe_ms: int = 4200,
    **kwargs: Any,
) -> OpenAiTtsProvider:
    """Build a provider with a mocked transport and a stubbed probe seam."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAiTtsProvider(
        base_url="https://api.openai.com/v1",
        api_key="k",
        sink=sink,
        client=client,
        **kwargs,
    )
    # Stub the single subprocess seam so synthesize() runs with no ffprobe binary.
    provider._probe_duration_ms = lambda path: probe_ms  # type: ignore[method-assign]
    return provider


def _synthesize(
    provider: OpenAiTtsProvider, *, text: str = "hello", voice: str = "alloy"
) -> SynthesizedSpeech:
    return asyncio.run(provider.synthesize(text=text, voice=voice))


# --- protocol + request/response mapping -----------------------------------


def test_satisfies_protocol(tmp_path: Path) -> None:
    provider = _provider(lambda r: _audio_resp(), sink=_FileSink(tmp_path))
    assert isinstance(provider, TTSProvider)


def test_builds_request_and_maps_response(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    sink = _FileSink(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return _audio_resp(b"ID3real-audio")

    provider = _provider(handler, sink=sink, probe_ms=4200)
    speech = _synthesize(provider, text="hi there", voice="nova")

    # Request shape (OpenAI /audio/speech: input/model/voice/response_format).
    assert seen["url"] == "https://api.openai.com/v1/audio/speech"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {
        "model": "tts-1",
        "input": "hi there",
        "voice": "nova",
        "response_format": "mp3",
    }

    # Response mapping into the descriptor; duration from the probe seam.
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms == 4200
    assert speech.voice == "nova"
    assert speech.produced_via == "tts:openai"
    assert speech.audio_uri.startswith("file://")

    # Raw audio bytes were handed to the storage sink (not embedded in the DTO).
    assert sink.received == [b"ID3real-audio"]


def test_model_and_response_format_overridable(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _audio_resp()

    provider = _provider(handler, sink=_FileSink(tmp_path), model="tts-1-hd", response_format="wav")
    _synthesize(provider, text="hi", voice="echo")
    assert seen["body"]["model"] == "tts-1-hd"
    assert seen["body"]["response_format"] == "wav"


def test_trailing_slash_base_url_normalized(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _audio_resp()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAiTtsProvider(
        base_url="https://api.openai.com/v1/",
        api_key="k",
        sink=_FileSink(tmp_path),
        client=client,
    )
    provider._probe_duration_ms = lambda path: 100  # type: ignore[method-assign]
    _synthesize(provider)
    assert seen["url"] == "https://api.openai.com/v1/audio/speech"


def test_http_error_surfaces(tmp_path: Path) -> None:
    provider = _provider(
        lambda r: httpx.Response(401, json={"error": "bad key"}),
        sink=_FileSink(tmp_path),
    )
    with pytest.raises(httpx.HTTPStatusError):
        _synthesize(provider)


def test_non_resolvable_sink_uri_fails_loud(tmp_path: Path) -> None:
    """A sink returning a non-file:// URI cannot be probed -> fail loud."""

    def mem_sink(audio: bytes) -> str:
        return "mem://audio/1.bin"

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _audio_resp()))
    provider = OpenAiTtsProvider(base_url="https://x/v1", api_key="k", sink=mem_sink, client=client)
    with pytest.raises(CompositionError):
        _synthesize(provider)


def test_missing_base_url_raises(tmp_path: Path) -> None:
    with pytest.raises(OpenAiTtsError):
        OpenAiTtsProvider(base_url="", api_key="k", sink=_FileSink(tmp_path))


def test_missing_model_raises(tmp_path: Path) -> None:
    with pytest.raises(OpenAiTtsError):
        OpenAiTtsProvider(base_url="https://x/v1", api_key="k", sink=_FileSink(tmp_path), model="")


# --- pure probe helpers (no ffprobe binary) --------------------------------


def test_build_ffprobe_args() -> None:
    args = build_ffprobe_args(Path("/tmp/a.mp3"))
    assert args == [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        "/tmp/a.mp3",
    ]


def test_parse_ffprobe_duration_ms_rounds_seconds() -> None:
    out = json.dumps({"format": {"duration": "4.2007"}})
    assert parse_ffprobe_duration_ms(out) == 4201


def test_parse_ffprobe_duration_ms_zero() -> None:
    out = json.dumps({"format": {"duration": "0.0"}})
    assert parse_ffprobe_duration_ms(out) == 0


def test_parse_ffprobe_duration_ms_missing_key_raises() -> None:
    with pytest.raises(OpenAiTtsError, match=r"format\.duration"):
        parse_ffprobe_duration_ms(json.dumps({"format": {}}))


def test_parse_ffprobe_duration_ms_not_a_number_raises() -> None:
    out = json.dumps({"format": {"duration": "N/A"}})
    with pytest.raises(OpenAiTtsError, match="not a number"):
        parse_ffprobe_duration_ms(out)


def test_parse_ffprobe_duration_ms_negative_raises() -> None:
    out = json.dumps({"format": {"duration": "-1.0"}})
    with pytest.raises(OpenAiTtsError, match="negative"):
        parse_ffprobe_duration_ms(out)


def test_parse_ffprobe_duration_ms_garbage_raises() -> None:
    with pytest.raises(OpenAiTtsError, match=r"format\.duration"):
        parse_ffprobe_duration_ms("not json at all")


# --- probe subprocess seam (mocked subprocess.run) -------------------------


def test_probe_missing_binary_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("app.media.tts.openai_tts.subprocess.run", boom)
    provider = OpenAiTtsProvider(base_url="https://x/v1", api_key="k", sink=_FileSink(tmp_path))
    with pytest.raises(OpenAiTtsError, match="ffprobe binary not found"):
        provider._probe_duration_ms(tmp_path / "a.mp3")


def test_probe_nonzero_exit_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sp

    def fail(*a: Any, **k: Any) -> _sp.CompletedProcess[bytes]:
        return _sp.CompletedProcess(args=a[0], returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr("app.media.tts.openai_tts.subprocess.run", fail)
    provider = OpenAiTtsProvider(base_url="https://x/v1", api_key="k", sink=_FileSink(tmp_path))
    with pytest.raises(OpenAiTtsError, match="exited with code 1"):
        provider._probe_duration_ms(tmp_path / "a.mp3")


def test_probe_success_parses_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sp

    stdout = json.dumps({"format": {"duration": "2.5"}}).encode()

    def ok(*a: Any, **k: Any) -> _sp.CompletedProcess[bytes]:
        return _sp.CompletedProcess(args=a[0], returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr("app.media.tts.openai_tts.subprocess.run", ok)
    provider = OpenAiTtsProvider(base_url="https://x/v1", api_key="k", sink=_FileSink(tmp_path))
    assert provider._probe_duration_ms(tmp_path / "a.mp3") == 2500
