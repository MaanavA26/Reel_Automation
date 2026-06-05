"""Live NVIDIA NIM TTS smoke test (real endpoint + key + ffprobe; skipped otherwise).

Run only this, after exporting the endpoint/key (from the ``backend/`` directory):

    REEL_NVIDIA_TTS_BASE_URL=... REEL_NVIDIA_API_KEY=... python -m pytest -m integration

It exercises the real path: NvidiaTtsProvider -> live NVIDIA speech endpoint ->
raw audio bytes -> tmp-file sink -> ffprobe duration probe -> `SynthesizedSpeech`.
The adapter is intentionally not wired into `Settings` (it stays out of
``config.py``), so this smoke test reads its endpoint/key/model/voice/format
straight from the environment and skips automatically when they (or the
``ffprobe`` binary) are absent — the default ``pytest`` run (and CI) never hit
the network.

The model/voice/response-format are env-parameterized so this first live call can
confirm the wire-contract assumption documented in ``app.media.tts.nvidia`` (and
ADR 0047) cheaply — without a code change for those knobs. A deeper shape change
(JSON→form, a different path/body) is a small edit isolated to the module's
``SPEECH_PATH`` + ``_build_payload``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.nvidia import DEFAULT_MODEL, DEFAULT_RESPONSE_FORMAT, NvidiaTtsProvider

pytestmark = pytest.mark.integration


def test_nvidia_tts_against_live_endpoint(tmp_path: Path) -> None:
    base_url = os.environ.get("REEL_NVIDIA_TTS_BASE_URL", "")
    api_key = os.environ.get("REEL_NVIDIA_API_KEY", "")
    if not base_url or not api_key:
        pytest.skip(
            "no live NVIDIA TTS endpoint/key configured "
            "(set REEL_NVIDIA_TTS_BASE_URL / REEL_NVIDIA_API_KEY)"
        )
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH (install ffmpeg)")

    response_format = os.environ.get("REEL_NVIDIA_TTS_FORMAT", DEFAULT_RESPONSE_FORMAT)

    def sink(audio: bytes) -> str:
        out = tmp_path / f"narration.{response_format}"
        out.write_bytes(audio)
        return out.as_uri()

    provider = NvidiaTtsProvider(
        base_url=base_url,
        api_key=api_key,
        sink=sink,
        model=os.environ.get("REEL_NVIDIA_TTS_MODEL", DEFAULT_MODEL),
        response_format=response_format,
    )
    voice = os.environ.get("REEL_NVIDIA_TTS_VOICE", "narrator")
    speech = asyncio.run(provider.synthesize(text="Reel Automation live test.", voice=voice))

    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms > 0, "live TTS returned a non-positive duration"
    assert speech.produced_via == "tts:nvidia"
    persisted = tmp_path / f"narration.{response_format}"
    assert persisted.exists() and persisted.stat().st_size > 0, "no audio bytes persisted"
