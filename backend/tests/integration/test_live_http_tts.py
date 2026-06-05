"""Live HTTP TTS smoke test (requires a real endpoint + key; skipped otherwise).

Run only this, after exporting the endpoint/key (from the `backend/` directory):

    REEL_TTS_BASE_URL=... REEL_TTS_API_KEY=... python -m pytest -m integration

It exercises the real path: HttpTtsProvider -> live REST TTS endpoint -> raw
audio bytes -> tmp-file sink -> `SynthesizedSpeech`. The adapter is intentionally
not wired into `Settings` (it stays out of `config.py`), so this smoke test reads
its endpoint/key straight from the environment and skips automatically when they
are absent — the default `pytest` run (and CI) never hit the network.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.http_tts import HttpTtsProvider

pytestmark = pytest.mark.integration


def test_http_tts_against_live_endpoint(tmp_path: Path) -> None:
    base_url = os.environ.get("REEL_TTS_BASE_URL", "")
    api_key = os.environ.get("REEL_TTS_API_KEY", "")
    if not base_url or not api_key:
        pytest.skip(
            "no live TTS endpoint/key configured (set REEL_TTS_BASE_URL / REEL_TTS_API_KEY)"
        )

    def sink(audio: bytes) -> str:
        out = tmp_path / "narration.bin"
        out.write_bytes(audio)
        return out.as_uri()

    provider = HttpTtsProvider(base_url=base_url, api_key=api_key, sink=sink)
    voice = os.environ.get("REEL_TTS_VOICE", "narrator")
    speech = asyncio.run(provider.synthesize(text="Reel Automation live test.", voice=voice))

    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms > 0, "live TTS returned a non-positive duration"
    assert speech.produced_via == "tts:http"
    persisted = Path(tmp_path / "narration.bin")
    assert persisted.exists() and persisted.stat().st_size > 0, "no audio bytes persisted"
