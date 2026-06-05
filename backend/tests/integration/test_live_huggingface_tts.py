"""Live HuggingFace TTS smoke test (requires an ``hf_`` key; skipped otherwise).

Run only this, after exporting your HuggingFace token (from ``backend/``). It
reads the operator's existing ``REEL_AUTOMATION_HUGGINGFACE_API_KEY`` (the key
ADR 0028 already added for the provider registry), falling back to a bespoke
``REEL_HF_API_KEY``:

    REEL_AUTOMATION_HUGGINGFACE_API_KEY=hf_... python -m pytest -m integration -k huggingface

It exercises the real path: HuggingFaceTtsProvider -> live HF Inference API ->
raw audio bytes -> ffprobe duration -> tmp-file sink -> ``SynthesizedSpeech``.
The adapter is intentionally not wired into ``Settings`` (it stays out of
``config.py``), so this smoke test reads its token/model straight from the
environment and skips automatically when the key (or the ``ffprobe`` binary) is
absent — the default ``pytest`` run (and CI) never hit the network.

Note: serverless HF TTS cold-starts; a first call to a sleeping model can raise
``HuggingFaceTtsError`` (estimated_time). Re-run shortly, or point the model at a
warm dedicated Inference Endpoint (``REEL_HF_TTS_API_ROOT``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.huggingface import DEFAULT_API_ROOT, HuggingFaceTtsProvider

pytestmark = pytest.mark.integration

#: A small serverless TTS model; override with ``REEL_HF_TTS_MODEL`` if it has
#: been deprecated or you prefer another (the model selection *is* the voice).
DEFAULT_MODEL = "espnet/kan-bayashi_ljspeech_vits"


def test_huggingface_tts_against_live_api(tmp_path: Path) -> None:
    token = os.environ.get("REEL_AUTOMATION_HUGGINGFACE_API_KEY", "") or os.environ.get(
        "REEL_HF_API_KEY", ""
    )
    if not token:
        pytest.skip("no HuggingFace key configured (set REEL_AUTOMATION_HUGGINGFACE_API_KEY)")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe binary not found (needed to probe HF audio duration)")

    def sink(audio: bytes) -> str:
        out = tmp_path / "narration.bin"
        out.write_bytes(audio)
        return out.as_uri()

    provider = HuggingFaceTtsProvider(
        model=os.environ.get("REEL_HF_TTS_MODEL", DEFAULT_MODEL),
        token=token,
        sink=sink,
        api_root=os.environ.get("REEL_HF_TTS_API_ROOT", DEFAULT_API_ROOT),
        timeout=120.0,
    )
    voice = os.environ.get("REEL_HF_TTS_VOICE", "narrator")
    speech = asyncio.run(provider.synthesize(text="Reel Automation live test.", voice=voice))

    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms > 0, "live HF TTS returned a non-positive duration"
    assert speech.produced_via == "tts:huggingface"
    persisted = Path(tmp_path / "narration.bin")
    assert persisted.exists() and persisted.stat().st_size > 0, "no audio bytes persisted"
