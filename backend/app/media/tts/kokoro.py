"""Local, zero-cost ``TTSProvider`` backed by the Kokoro ONNX model.

This is the *primary* TTS backend for Reel Automation: it runs the Apache-2.0
`Kokoro-82M <https://hf.co/hexgrad/Kokoro-82M>`_ model entirely on the user's
machine via ONNX Runtime (CPU) — no network, no vendor, no per-call cost. It is
the local-first counterpart to the network ``HttpTtsProvider`` (ADR 0022) behind
the same ADR 0019 media seam, and like it is a deterministic **tool** (CLAUDE.md
§4 — text in, audio out; the upstream strategist decides *what* to say).

Design — the load-bearing split (mirrors `ffmpeg.py`'s pure/impure seam, ADR
0023, and the advisor's seam guidance):

* **Inference is the single impure seam,** `_create_waveform`: it lazy-imports
  the ``kokoro_onnx`` package, builds+caches the model on first use, and calls
  ``Kokoro.create`` to get a ``(samples, sample_rate)`` waveform. This is the one
  mockable point and the only thing that touches the model.
* **Everything after it is pure code** outside the seam: `encode_wav_pcm16`
  encodes the waveform to WAV bytes (stdlib ``wave``, **no numpy**, so it is
  testable with a plain list) and the duration is computed exactly from
  ``len(samples) / sample_rate``.

Because Kokoro returns a real waveform, the clip duration is **exact** from the
sample count — there is no need for the format-specific duration header the HTTP
adapter requires, nor for an ``ffprobe`` fallback (which would be dead code; §7
no-overbuild).

Offline-clean by construction: the ``kokoro_onnx`` import lives *inside* the
inference method (mirroring how ``pypdf`` is lazy-imported in
`services.ingestion.pdf_parser`), so importing this module never requires the
package — a missing dependency fails loud, at synth time, with an install hint.
The async `synthesize` runs the blocking CPU inference off the event loop via
``asyncio.to_thread`` (mirroring `ffmpeg.py`'s `_run`).

Storage seam: the produced WAV bytes are an opaque blob *owned by storage* (ADR
0019 — the media layer traffics in descriptors). So this adapter does not choose
where audio lives; an `AudioSink` callable is injected at construction (the same
contract `HttpTtsProvider` uses) and must return a ``file://`` URI — the scheme
the ffmpeg composition adapter resolves (`composition.ffmpeg.resolve_local_path`).
"""

from __future__ import annotations

import array
import asyncio
import io
import wave
from collections.abc import Iterable
from typing import Any

from app.media.schemas import SynthesizedSpeech
from app.media.tts.http_tts import AudioSink

PROVIDER_NAME = "kokoro"

#: The pip package that ships the local Kokoro ONNX runtime + the ``Kokoro``
#: class this adapter drives. Surfaced in the install hint on a missing import.
PIP_PACKAGE = "kokoro-onnx"

#: Default Kokoro voice id (American-English female "Heart"). Overridable per
#: call via the protocol's ``voice`` argument, which always wins (see
#: ``synthesize``); this is only the constructor default.
DEFAULT_VOICE = "af_heart"

#: Kokoro's native synthesis language code (American English).
DEFAULT_LANG = "en-us"


class KokoroTtsError(RuntimeError):
    """Raised when local Kokoro synthesis cannot be performed.

    Covers the fail-loud cases this adapter owns: the ``kokoro_onnx`` package is
    not installed (with an install hint), the model/voices files are missing, or
    the inference call fails. Mirrors `HttpTtsError` / `CompositionError` — one
    local error type so callers handle synthesis failure uniformly.
    """


def encode_wav_pcm16(samples: Iterable[float], sample_rate: int) -> bytes:
    """Encode a mono float waveform to 16-bit PCM WAV bytes. Pure, stdlib-only.

    Kokoro returns float32 samples in roughly ``[-1.0, 1.0]`` (the documented
    model output range). Each sample is clamped to that range and scaled to a
    signed 16-bit integer, then written as a single-channel WAV via the stdlib
    ``wave`` module — **no numpy**, so this is unit-testable with a plain list
    (and the inference seam can be mocked without numpy/onnx installed).

    ``sample_rate`` must be positive; an empty waveform yields a valid, empty
    WAV (a zero-length clip), which the caller may treat as it sees fit.
    """
    if sample_rate <= 0:
        raise KokoroTtsError(f"sample_rate must be positive, got {sample_rate}")

    pcm = array.array("h")  # signed 16-bit
    for sample in samples:
        # Clamp to [-1, 1] then scale to int16; 32767 keeps the positive peak in
        # range (a raw *32768 would overflow at +1.0).
        clamped = -1.0 if sample < -1.0 else (1.0 if sample > 1.0 else sample)
        pcm.append(int(clamped * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def duration_ms_from_samples(sample_count: int, sample_rate: int) -> int:
    """Exact clip duration in integer milliseconds from the sample count. Pure.

    Because Kokoro returns the waveform itself, duration is recovered exactly
    (no format parser, no ``ffprobe``): ``samples / rate`` seconds → ms, rounded
    to the nearest millisecond. Satisfies the ``SynthesizedSpeech.duration_ms``
    ``ge=0`` contract for any non-negative input.
    """
    if sample_rate <= 0:
        raise KokoroTtsError(f"sample_rate must be positive, got {sample_rate}")
    if sample_count < 0:
        raise KokoroTtsError(f"sample_count must be non-negative, got {sample_count}")
    return round(sample_count * 1000 / sample_rate)


class KokoroTtsProvider:
    """A `TTSProvider` backed by the local Kokoro ONNX model (offline, no cost).

    Construction takes the model configuration — the ONNX ``model_path`` and the
    ``voices_path`` (kokoro-onnx needs both files), a default ``voice`` and
    ``lang``, and ``speed`` — **not** global ``Settings`` (config-root-agnostic,
    mirroring `HttpTtsProvider`; the future composition root supplies these). The
    audio `sink` is injected (the same storage-neutral contract): it persists the
    WAV bytes and returns a ``file://`` URI.

    The ``kokoro_onnx`` package and the ONNX model are loaded lazily on the first
    ``synthesize`` call and the model is **cached** thereafter (the ONNX graph is
    expensive to build; reloading it per call would be wasteful). Module import
    stays offline-clean because the import lives inside the inference seam.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model_path: str,
        voices_path: str,
        sink: AudioSink,
        voice: str = DEFAULT_VOICE,
        lang: str = DEFAULT_LANG,
        speed: float = 1.0,
    ) -> None:
        if not model_path:
            raise KokoroTtsError("model_path is required")
        if not voices_path:
            raise KokoroTtsError("voices_path is required")
        self._model_path = model_path
        self._voices_path = voices_path
        self._sink = sink
        # The protocol's ``synthesize(*, text, voice)`` makes ``voice`` required,
        # so the per-call value always wins (see ``synthesize``); this constructor
        # ``voice`` is the config-surface default a future wiring/composition root
        # can record (e.g. a channel's `tts_voice_id`) — it is intentionally not
        # consulted on the protocol path, so it is exposed but not read here.
        self._voice = voice
        self._lang = lang
        self._speed = speed
        # Lazily built + cached on first synth (see ``_ensure_model``); typed
        # loosely so mypy does not demand the (offline-absent) kokoro_onnx stubs.
        self._kokoro: Any | None = None

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        """Synthesize ``text`` into a local WAV clip and return its descriptor.

        Runs the blocking CPU inference off the event loop (``asyncio.to_thread``,
        mirroring `ffmpeg.py`'s ``_run``), encodes the waveform to WAV bytes,
        persists them via the injected `sink`, and returns a `SynthesizedSpeech`
        whose ``duration_ms`` is computed exactly from the sample count. The
        protocol's per-call ``voice`` **wins** over the constructor default.
        Raises `KokoroTtsError` if ``kokoro_onnx`` is unavailable or inference
        fails.
        """
        samples, sample_rate = await asyncio.to_thread(self._create_waveform, text, voice)

        audio_bytes = encode_wav_pcm16(samples, sample_rate)
        duration_ms = duration_ms_from_samples(len(samples), sample_rate)
        audio_uri = self._sink(audio_bytes)

        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )

    def _create_waveform(self, text: str, voice: str) -> tuple[Any, int]:
        """The single impure inference seam: text → ``(samples, sample_rate)``.

        Lazy-imports ``kokoro_onnx`` (so module import stays offline-clean),
        builds+caches the ``Kokoro`` model on first call, and runs synthesis.
        Returns the raw waveform (a float sample sequence) and its sample rate;
        all encoding/duration math is pure and lives outside this seam (mockable
        as one point). Normalizes a missing package / model files / inference
        failure to `KokoroTtsError` with an actionable hint.
        """
        kokoro = self._ensure_model()
        try:
            samples, sample_rate = kokoro.create(
                text,
                voice=voice,
                speed=self._speed,
                lang=self._lang,
            )
        except Exception as exc:  # kokoro_onnx surfaces a variety of runtime errors
            raise KokoroTtsError(f"Kokoro synthesis failed: {type(exc).__name__}: {exc}") from exc
        return samples, sample_rate

    def _ensure_model(self) -> Any:
        """Lazy-import ``kokoro_onnx`` and build+cache the ``Kokoro`` model.

        The import is inside this method (never at construction/module import) so
        the module imports clean in the offline build sandbox, mirroring how
        ``pypdf`` is lazy-imported in `services.ingestion.pdf_parser`. A missing
        package fails loud with the pip install hint; a missing/invalid model or
        voices file fails loud with a path hint.
        """
        if self._kokoro is not None:
            return self._kokoro
        try:
            # Lazy import keeps module import offline-clean (mirrors pypdf, ADR
            # 0014); the ignore is *used* in the offline sandbox where the package
            # is absent, so `warn_unused_ignores` stays quiet, and is a no-op once
            # installed. Kept inline so `pyproject.toml` is untouched (§9 scope).
            from kokoro_onnx import Kokoro  # type: ignore[import-not-found]
        except ImportError as exc:
            raise KokoroTtsError(
                f"the {PIP_PACKAGE!r} package is not installed "
                f"(install it with `pip install {PIP_PACKAGE}` and download the "
                f"kokoro ONNX model + voices files)"
            ) from exc
        try:
            self._kokoro = Kokoro(self._model_path, self._voices_path)
        except Exception as exc:  # missing/invalid model or voices file
            raise KokoroTtsError(
                f"could not load the Kokoro model from model_path="
                f"{self._model_path!r}, voices_path={self._voices_path!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return self._kokoro
