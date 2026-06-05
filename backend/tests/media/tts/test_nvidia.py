"""Tests for the NVIDIA NIM TTS provider adapter.

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, a
tmp-file ``sink`` stands in for storage, and the single ``ffprobe`` subprocess
seam (``_probe_duration_ms``) is stubbed — so request building, the bytes →
``SynthesizedSpeech`` mapping, and the duration-probe contract are all verified
without a live call or the ``ffprobe`` binary. The live call itself is covered by
a separate ``@pytest.mark.integration`` smoke test (skipped without creds).
Mirrors ``tests/media/tts/test_http_tts.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.nvidia import (
    DEFAULT_MODEL,
    DEFAULT_RESPONSE_FORMAT,
    SPEECH_PATH,
    NvidiaTtsError,
    NvidiaTtsProvider,
    build_ffprobe_args,
    parse_ffprobe_duration_ms,
)


class _FileSink:
    """An ``AudioSink`` that writes bytes to a tmp dir and returns a file:// URI."""

    def __init__(self, tmp_path: Path) -> None:
        self._dir = tmp_path
        self.received: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.received.append(audio)
        out = self._dir / f"audio-{len(self.received)}.mp3"
        out.write_bytes(audio)
        return out.as_uri()


def _provider(
    handler: Any,
    *,
    sink: Any,
    model: str = DEFAULT_MODEL,
    response_format: str = DEFAULT_RESPONSE_FORMAT,
) -> NvidiaTtsProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NvidiaTtsProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="nvapi-secret",
        sink=sink,
        model=model,
        response_format=response_format,
        client=client,
    )


def _synthesize(
    provider: NvidiaTtsProvider,
    *,
    text: str = "hello",
    voice: str = "narrator",
    duration_ms: int = 4200,
) -> SynthesizedSpeech:
    """Run ``synthesize`` with the ffprobe seam stubbed to a known duration."""

    def fake_probe(_self: NvidiaTtsProvider, _path: Path) -> int:
        return duration_ms

    original = NvidiaTtsProvider._probe_duration_ms
    NvidiaTtsProvider._probe_duration_ms = fake_probe  # type: ignore[method-assign]
    try:
        return asyncio.run(provider.synthesize(text=text, voice=voice))
    finally:
        NvidiaTtsProvider._probe_duration_ms = original  # type: ignore[method-assign]


def test_satisfies_protocol(tmp_path: Path) -> None:
    provider = _provider(lambda r: httpx.Response(200, content=b""), sink=_FileSink(tmp_path))
    assert isinstance(provider, TTSProvider)


def test_builds_request_and_maps_response(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    sink = _FileSink(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3fake-mp3-bytes")

    speech = _synthesize(
        _provider(handler, sink=sink), text="hi there", voice="anchor", duration_ms=4200
    )

    # Request shape (the wire-contract assumption to confirm live).
    assert seen["url"] == f"https://integrate.api.nvidia.com/v1{SPEECH_PATH}"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer nvapi-secret"
    assert seen["body"] == {
        "model": DEFAULT_MODEL,
        "input": "hi there",
        "voice": "anchor",
        "response_format": DEFAULT_RESPONSE_FORMAT,
    }

    # Response mapping into the descriptor.
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms == 4200
    assert speech.voice == "anchor"
    assert speech.produced_via == "tts:nvidia"
    assert speech.audio_uri.startswith("file://")

    # Raw audio bytes were handed to the storage sink (not embedded in the DTO).
    assert sink.received == [b"ID3fake-mp3-bytes"]


def test_model_and_response_format_overridable(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"x")

    provider = _provider(
        handler, sink=_FileSink(tmp_path), model="fastpitch-hifigan-en-us", response_format="wav"
    )
    _synthesize(provider)
    assert seen["body"]["model"] == "fastpitch-hifigan-en-us"
    assert seen["body"]["response_format"] == "wav"


def test_trailing_slash_base_url_normalized(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"x")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NvidiaTtsProvider(
        base_url="https://integrate.api.nvidia.com/v1/",
        api_key="k",
        sink=_FileSink(tmp_path),
        client=client,
    )
    _synthesize(provider)
    assert seen["url"] == f"https://integrate.api.nvidia.com/v1{SPEECH_PATH}"


def test_http_error_surfaces(tmp_path: Path) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _synthesize(
            _provider(
                lambda r: httpx.Response(503, json={"error": "down"}), sink=_FileSink(tmp_path)
            )
        )


def test_api_key_never_leaks_in_repr(tmp_path: Path) -> None:
    provider = _provider(lambda r: httpx.Response(200, content=b""), sink=_FileSink(tmp_path))
    assert "nvapi-secret" not in repr(provider)


def test_missing_base_url_raises(tmp_path: Path) -> None:
    with pytest.raises(NvidiaTtsError, match="base_url"):
        NvidiaTtsProvider(base_url="", api_key="k", sink=_FileSink(tmp_path))


def test_missing_model_raises(tmp_path: Path) -> None:
    with pytest.raises(NvidiaTtsError, match="model"):
        NvidiaTtsProvider(base_url="https://x/v1", api_key="k", sink=_FileSink(tmp_path), model="")


# --- duration probe: the subprocess seam (no ffprobe binary) ----------------


def test_probe_runs_ffprobe_and_maps_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end with the *subprocess* stubbed (not the whole probe method)."""
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        stdout = json.dumps({"format": {"duration": "3.5"}}).encode()
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr("app.media.tts.nvidia.subprocess.run", fake_run)

    sink = _FileSink(tmp_path)
    provider = _provider(lambda r: httpx.Response(200, content=b"audio"), sink=sink)
    speech = asyncio.run(provider.synthesize(text="hi", voice="v"))

    assert captured["args"][0] == "ffprobe"
    assert speech.duration_ms == 3500


def test_probe_missing_binary_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("app.media.tts.nvidia.subprocess.run", fake_run)
    provider = _provider(lambda r: httpx.Response(200, content=b"x"), sink=_FileSink(tmp_path))
    with pytest.raises(NvidiaTtsError, match="ffprobe binary not found"):
        asyncio.run(provider.synthesize(text="hi", voice="v"))


def test_probe_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr("app.media.tts.nvidia.subprocess.run", fake_run)
    provider = _provider(lambda r: httpx.Response(200, content=b"x"), sink=_FileSink(tmp_path))
    with pytest.raises(NvidiaTtsError, match="exited with code 1"):
        asyncio.run(provider.synthesize(text="hi", voice="v"))


def test_non_file_sink_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``mem://`` sink cannot be probed -> resolve_local_path fails loud."""

    def fake_run(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:  # pragma: no cover
        raise AssertionError("ffprobe should not run for an unresolvable URI")

    monkeypatch.setattr("app.media.tts.nvidia.subprocess.run", fake_run)

    def mem_sink(_audio: bytes) -> str:
        return "mem://audio/1.bin"

    provider = _provider(lambda r: httpx.Response(200, content=b"x"), sink=mem_sink)
    with pytest.raises(Exception, match="scheme"):
        asyncio.run(provider.synthesize(text="hi", voice="v"))


# --- pure probe helpers (no ffprobe binary) ---------------------------------


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
    out = json.dumps({"format": {"duration": "4.2009"}})
    assert parse_ffprobe_duration_ms(out) == 4201


def test_parse_ffprobe_duration_ms_zero() -> None:
    assert parse_ffprobe_duration_ms(json.dumps({"format": {"duration": "0"}})) == 0


def test_parse_ffprobe_duration_ms_missing_key_raises() -> None:
    with pytest.raises(NvidiaTtsError, match=r"format\.duration"):
        parse_ffprobe_duration_ms(json.dumps({"format": {}}))


def test_parse_ffprobe_duration_ms_not_a_number_raises() -> None:
    out = json.dumps({"format": {"duration": "N/A"}})
    with pytest.raises(NvidiaTtsError, match="not a number"):
        parse_ffprobe_duration_ms(out)


def test_parse_ffprobe_duration_ms_negative_raises() -> None:
    out = json.dumps({"format": {"duration": "-1.0"}})
    with pytest.raises(NvidiaTtsError, match="negative"):
        parse_ffprobe_duration_ms(out)


def test_parse_ffprobe_duration_ms_garbage_raises() -> None:
    with pytest.raises(NvidiaTtsError, match=r"format\.duration"):
        parse_ffprobe_duration_ms("not json at all")
