"""Tests for the local Kokoro ONNX TTS provider.

Fully offline and dependency-free: the impure inference seam (`_create_waveform`)
is mocked to return a ``(samples, sample_rate)`` waveform — a plain ``list`` of
floats — so the pure WAV encoding and the exact samples/rate duration math are
verified **without** ``kokoro_onnx``, ``onnxruntime``, or ``numpy`` installed.
The live model path is the separate ``@pytest.mark.integration`` smoke test at
the bottom, which skips when the package or model files are absent (mirroring the
ffmpeg adapter's binary-skip). Mirrors ``tests/media/tts/test_http_tts.py``.
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.kokoro import (
    DEFAULT_VOICE,
    PIP_PACKAGE,
    KokoroTtsError,
    KokoroTtsProvider,
    duration_ms_from_samples,
    encode_wav_pcm16,
)


class _CapturingSink:
    """An in-memory ``AudioSink`` that records bytes and returns a ``file://`` URI."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def __call__(self, audio: bytes) -> str:
        self.received.append(audio)
        return f"file:///tmp/kokoro/{len(self.received)}.wav"


def _provider(sink: Any | None = None, **kwargs: Any) -> KokoroTtsProvider:
    return KokoroTtsProvider(
        model_path="kokoro-v1.0.onnx",
        voices_path="voices-v1.0.bin",
        sink=sink or _CapturingSink(),
        **kwargs,
    )


def _synthesize(
    provider: KokoroTtsProvider, *, text: str = "hello", voice: str = "narrator"
) -> SynthesizedSpeech:
    return asyncio.run(provider.synthesize(text=text, voice=voice))


# --- encode_wav_pcm16 (pure, stdlib-only) ----------------------------------


def test_encode_wav_produces_parseable_riff_with_metadata() -> None:
    # 24000 mono samples at 24kHz == 1 second of audio.
    audio = encode_wav_pcm16([0.0] * 24000, 24000)
    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24000
        assert wav.getnframes() == 24000


def test_encode_wav_clamps_out_of_range_samples() -> None:
    # Samples beyond [-1, 1] must not overflow int16 — they clamp to the peaks.
    audio = encode_wav_pcm16([2.0, -2.0, 0.0], 24000)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        frames = wav.readframes(3)
    import struct

    values = struct.unpack("<3h", frames)
    assert values == (32767, -32767, 0)


def test_encode_wav_empty_waveform_is_valid_empty_clip() -> None:
    audio = encode_wav_pcm16([], 24000)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnframes() == 0


def test_encode_wav_rejects_nonpositive_rate() -> None:
    with pytest.raises(KokoroTtsError, match="sample_rate must be positive"):
        encode_wav_pcm16([0.0], 0)


# --- duration_ms_from_samples (pure) ---------------------------------------


def test_duration_is_exact_from_sample_count() -> None:
    assert duration_ms_from_samples(24000, 24000) == 1000  # 1.0s
    assert duration_ms_from_samples(48000, 24000) == 2000  # 2.0s
    assert duration_ms_from_samples(12000, 24000) == 500  # 0.5s
    assert duration_ms_from_samples(0, 24000) == 0


def test_duration_rejects_bad_inputs() -> None:
    with pytest.raises(KokoroTtsError, match="sample_rate must be positive"):
        duration_ms_from_samples(100, 0)
    with pytest.raises(KokoroTtsError, match="sample_count must be non-negative"):
        duration_ms_from_samples(-1, 24000)


# --- synthesize (inference seam mocked; no kokoro/onnx/numpy needed) --------


def test_satisfies_protocol() -> None:
    assert isinstance(_provider(), TTSProvider)


def test_synthesize_maps_waveform_to_descriptor_and_sinks_wav() -> None:
    sink = _CapturingSink()
    provider = _provider(sink)
    # 48000 samples @ 24kHz == exactly 2000ms — the duration is computed from the
    # produced audio, not handed in.
    with patch.object(
        provider, "_create_waveform", return_value=([0.0] * 48000, 24000)
    ) as mock_create:
        speech = _synthesize(provider, text="hi there", voice="anchor")

    # The per-call voice was passed through to inference and recorded on the DTO.
    mock_create.assert_called_once_with("hi there", "anchor")
    assert isinstance(speech, SynthesizedSpeech)
    assert speech.duration_ms == 2000
    assert speech.voice == "anchor"
    assert speech.produced_via == "tts:kokoro"
    # A real WAV blob (not the samples) reached the storage sink, and the
    # descriptor points at the file:// URI it returned.
    assert speech.audio_uri == "file:///tmp/kokoro/1.wav"
    assert len(sink.received) == 1
    assert sink.received[0][:4] == b"RIFF"


def test_synthesize_per_call_voice_overrides_constructor_default() -> None:
    provider = _provider(voice="am_adam")
    with patch.object(provider, "_create_waveform", return_value=([0.0], 24000)) as mock_create:
        speech = _synthesize(provider, voice="bm_george")
    # The protocol's per-call voice wins over the constructor default.
    assert mock_create.call_args.args[1] == "bm_george"
    assert speech.voice == "bm_george"


def test_default_voice_is_a_real_kokoro_id() -> None:
    # The constructor default is a documented kokoro voice (sanity guard).
    assert DEFAULT_VOICE == "af_heart"


# --- _ensure_model: lazy import + fail-loud (no kokoro installed) ----------


def test_missing_package_fails_loud_with_install_hint() -> None:
    provider = _provider()
    # Simulate the offline build sandbox: the import inside _ensure_model raises.
    with patch.dict("sys.modules", {"kokoro_onnx": None}):
        with pytest.raises(KokoroTtsError, match=PIP_PACKAGE):
            provider._ensure_model()


def test_inference_failure_normalized_to_kokoro_error() -> None:
    provider = _provider()
    boom = RuntimeError("onnx blew up")

    class _Model:
        def create(self, *a: Any, **k: Any) -> Any:
            raise boom

    # Pre-seed the cached model so _create_waveform reaches .create().
    provider._kokoro = _Model()
    with pytest.raises(KokoroTtsError, match="Kokoro synthesis failed"):
        provider._create_waveform("hi", "narrator")


def test_model_is_cached_across_calls() -> None:
    provider = _provider()
    sentinel = object()
    provider._kokoro = sentinel  # type: ignore[assignment]
    # _ensure_model returns the cached instance without re-importing/rebuilding.
    assert provider._ensure_model() is sentinel


def test_missing_paths_rejected_at_construction() -> None:
    with pytest.raises(KokoroTtsError, match="model_path is required"):
        KokoroTtsProvider(model_path="", voices_path="v.bin", sink=_CapturingSink())
    with pytest.raises(KokoroTtsError, match="voices_path is required"):
        KokoroTtsProvider(model_path="m.onnx", voices_path="", sink=_CapturingSink())


# --- integration: real local Kokoro synthesis (skips without pkg + models) --


@pytest.mark.integration
def test_real_kokoro_synthesis(tmp_path: Path) -> None:
    """Synthesize a real clip with the local Kokoro model.

    Skips unless ``kokoro_onnx`` is installed **and** the model + voices files
    are present (their paths read from ``REEL_KOKORO_MODEL_PATH`` /
    ``REEL_KOKORO_VOICES_PATH``), mirroring the ffmpeg adapter's binary check.
    """
    pytest.importorskip("kokoro_onnx")
    model_path = os.environ.get("REEL_KOKORO_MODEL_PATH")
    voices_path = os.environ.get("REEL_KOKORO_VOICES_PATH")
    if not model_path or not voices_path:
        pytest.skip("REEL_KOKORO_MODEL_PATH / REEL_KOKORO_VOICES_PATH not set")
    if not Path(model_path).exists() or not Path(voices_path).exists():
        pytest.skip("kokoro model / voices files not found at the configured paths")

    written: list[bytes] = []

    def sink(audio: bytes) -> str:
        out = tmp_path / "narration.wav"
        out.write_bytes(audio)
        written.append(audio)
        return out.as_uri()

    provider = KokoroTtsProvider(
        model_path=model_path, voices_path=voices_path, sink=sink, voice=DEFAULT_VOICE
    )
    speech = asyncio.run(
        provider.synthesize(text="Reel Automation runs Kokoro locally.", voice=DEFAULT_VOICE)
    )

    assert speech.produced_via == "tts:kokoro"
    assert speech.duration_ms > 0
    assert speech.audio_uri.startswith("file://")
    assert written and written[0][:4] == b"RIFF"
