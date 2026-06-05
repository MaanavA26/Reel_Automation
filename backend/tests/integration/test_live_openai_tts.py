"""Live OpenAI ``/audio/speech`` smoke test (real endpoint + key + ffprobe).

Run only this, after exporting the endpoint/key (from the `backend/` directory):

    REEL_OPENAI_TTS_BASE_URL=... REEL_OPENAI_TTS_API_KEY=... \
        python -m pytest -m integration

It exercises the real path: OpenAiTtsProvider -> live /audio/speech endpoint ->
raw audio bytes -> tmp-file sink -> ffprobe duration probe -> `SynthesizedSpeech`.
The adapter is intentionally not wired into `Settings` (it stays out of
`config.py`), so this smoke test reads its endpoint/key straight from the
environment and skips automatically when they (or the ffprobe binary) are absent
— the default `pytest` run (and CI) never hit the network.

`REEL_OPENAI_TTS_BASE_URL` defaults to OpenAI's API; override it for any
compatible backend. `REEL_OPENAI_TTS_VOICE` / `REEL_OPENAI_TTS_MODEL` are
optional overrides.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.openai_tts import DEFAULT_MODEL, OpenAiTtsProvider

pytestmark = pytest.mark.integration


def test_openai_tts_against_live_endpoint(tmp_path: Path) -> None:
    api_key = os.environ.get("REEL_OPENAI_TTS_API_KEY", "")
    if not api_key:
        pytest.skip("no OpenAI TTS key configured (set REEL_OPENAI_TTS_API_KEY)")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH (install ffmpeg)")

    base_url = os.environ.get("REEL_OPENAI_TTS_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("REEL_OPENAI_TTS_MODEL", DEFAULT_MODEL)
    voice = os.environ.get("REEL_OPENAI_TTS_VOICE", "alloy")

    def sink(audio: bytes) -> str:
        out = tmp_path / "narration.mp3"
        out.write_bytes(audio)
        return out.as_uri()

    provider = OpenAiTtsProvider(base_url=base_url, api_key=api_key, sink=sink, model=model)
    speech = asyncio.run(provider.synthesize(text="Reel Automation live test.", voice=voice))

    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms > 0, "live TTS returned a non-positive probed duration"
    assert speech.produced_via == "tts:openai"
    persisted = Path(tmp_path / "narration.mp3")
    assert persisted.exists() and persisted.stat().st_size > 0, "no audio bytes persisted"
