"""HuggingFace Inference API ``TTSProvider`` adapter (httpx-based).

The second concrete `TTSProvider` behind the ADR 0019 media seam (sibling of the
generic-REST `HttpTtsProvider`, ADR 0022). It speaks the **HuggingFace serverless
Inference API** for a hosted text-to-speech model, selected at construction by
``model`` + an ``hf_`` token — so the operator's existing HuggingFace key drives
narration with no new vendor account (CLAUDE.md §6 provider abstraction). See
ADR 0048.

Per CLAUDE.md §4 this is a deterministic **tool** (text in, audio out — no
judgment): the upstream content strategist decides *what* to say and *which voice
model* to use; this adapter *executes* the synthesis. Built on ``httpx`` (already
a runtime dependency) so request building and the bytes → ``SynthesizedSpeech``
mapping are unit-testable **offline** via ``httpx.MockTransport``; only the live
call needs network.

The wire contract this adapter targets:

- ``POST {api_root}/models/{model}`` with an ``Authorization: Bearer {token}``
  header and the JSON body ``{"inputs": text}``. (The HF text-to-speech task
  takes a bare ``inputs`` string; there is no generic *voice* selector — the
  *model* is the voice. ``voice`` is therefore echoed into the descriptor for
  provenance but never sent; per-model speaker params are a later extension.)
- On success the response body is the **raw audio bytes** (FLAC/WAV, depending on
  the model). On error HF returns a JSON envelope — sometimes still with ``200``
  — so the response content-type is checked before the bytes are treated as
  audio.
- **Model cold-start.** A serverless model that is not loaded returns ``503``
  with a JSON body carrying ``estimated_time`` (seconds until it is ready). This
  adapter does **not** sleep in-process (retries/budgets are the orchestrator's
  concern — ADR 0022/0027); it raises a typed `HuggingFaceTtsError` carrying the
  estimate so the caller can retry. HF's native ``options.wait_for_model`` flag
  is an alternative (see ADR 0048); explicit 503 handling keeps the adapter
  time-decoupled and consistent with the repo's "errors propagate" boundary.

Duration: HF returns no clip duration and ``SynthesizedSpeech.duration_ms`` is a
required ``ge=0`` field, so it is computed by piping the audio bytes through
``ffprobe`` (``-i pipe:0``). Probing the *bytes* (not a file path) keeps duration
independent of where the injected ``sink`` puts the audio — an in-memory or
object-store sink still works. The ``ffprobe`` call is the single mockable exec
seam (``_probe_duration_ms``), run off the event loop, mirroring the
`FfmpegCompositionService._run` shape (ADR 0023).

Storage seam: the audio bytes are an opaque blob *owned by storage* (ADR 0019 /
``SynthesizedSpeech`` docstring), so an ``AudioSink`` callable is injected at
construction (mirroring the injected ``client``): it persists the bytes and
returns the ``audio_uri`` to record. This adapter reuses the ``AudioSink`` alias
from `http_tts` rather than redefining it (one media-layer storage seam, §7).

Operational note: serverless HuggingFace TTS has **cold-starts and rate limits**
(fine for testing / low volume). For production scale, point ``api_root`` at a
dedicated paid **Inference Endpoint** (a warm, rate-stable URL) — a config change,
no code change.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from typing import Any

import httpx

from app.media.schemas import SynthesizedSpeech
from app.media.tts.http_tts import AudioSink

PROVIDER_NAME = "huggingface"

#: Default HuggingFace serverless Inference API root.
DEFAULT_API_ROOT = "https://api-inference.huggingface.co"

#: How many trailing chars of ffprobe's stderr to surface in an error.
_STDERR_TAIL = 2000

#: Bound the upstream-body excerpt in error messages so a full provider response
#: never lands in logs / errors (info-leak guard, mirrors ADR 0043).
_ERR_BODY_MAX = 500


class HuggingFaceTtsError(RuntimeError):
    """Raised on a contract-violating HF response or a duration-probe failure.

    Transport-level operational failures (timeout, generic 5xx via
    ``raise_for_status``) surface as their native ``httpx`` exceptions, mirroring
    the LLM/TTS adapters; this class covers the HF-specific failures the
    orchestrator must distinguish: a model **cold-start** (503 +
    ``estimated_time``), a non-audio (JSON error) success body, and a failed
    ``ffprobe`` duration probe. The ``hf_`` token never appears in its message.
    """


class HuggingFaceTtsProvider:
    """A ``TTSProvider`` over the HuggingFace serverless Inference API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model: str,
        token: str,
        sink: AudioSink,
        api_root: str = DEFAULT_API_ROOT,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        ffprobe_bin: str = "ffprobe",
    ) -> None:
        if not model:
            raise HuggingFaceTtsError("model is required")
        if not token:
            raise HuggingFaceTtsError("token is required")
        self._model = model
        self._token = token
        self._sink = sink
        self._api_root = api_root.rstrip("/")
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._ffprobe_bin = ffprobe_bin

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        """Synthesize ``text`` to speech via the HF model; return a descriptor.

        ``voice`` is recorded for provenance but not sent (the *model* is the
        voice — see the module docstring). Raises `HuggingFaceTtsError` on a model
        cold-start (503 + ``estimated_time``), a non-audio response body, or a
        failed duration probe; other operational failures propagate as ``httpx``
        errors.
        """
        response = await self._client.post(
            f"{self._api_root}/models/{self._model}",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"inputs": text},
        )

        # Inspect the cold-start 503 *before* raise_for_status so its estimate is
        # surfaced as a clear, actionable error rather than a bare HTTPStatusError.
        if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            raise HuggingFaceTtsError(_cold_start_message(response))
        response.raise_for_status()

        _reject_non_audio(response)

        audio = response.content
        duration_ms = await asyncio.to_thread(self._probe_duration_ms, audio)
        audio_uri = self._sink(audio)

        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )

    def _probe_duration_ms(self, audio: bytes) -> int:
        """Probe the audio bytes for their duration in ms via ``ffprobe``.

        The single mockable exec seam (mirroring `FfmpegCompositionService._run`,
        ADR 0023): pipes the bytes to ``ffprobe -i pipe:0`` and parses the
        container duration in seconds. Probing the *bytes* (not a path) keeps
        duration independent of the injected ``sink``. A missing binary, a
        timeout, a non-zero exit, or an unparseable duration all normalize to one
        `HuggingFaceTtsError`.
        """
        args = [
            self._ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "-i",
            "pipe:0",
        ]
        try:
            result = subprocess.run(
                args,
                input=audio,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise HuggingFaceTtsError(
                f"ffprobe binary not found (is it installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HuggingFaceTtsError(
                f"ffprobe timed out after {self._timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise HuggingFaceTtsError(
                f"ffprobe exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )

        raw = result.stdout.decode("utf-8", errors="replace").strip()
        try:
            seconds = float(raw)
        except ValueError as exc:
            raise HuggingFaceTtsError(f"ffprobe returned an unparseable duration: {raw!r}") from exc
        if seconds < 0:
            raise HuggingFaceTtsError(f"ffprobe returned a negative duration: {seconds}")
        return round(seconds * 1000)


def _cold_start_message(response: httpx.Response) -> str:
    """Build a clear cold-start error, surfacing ``estimated_time`` when present."""
    estimate: float | None = None
    try:
        body: Any = response.json()
        raw = body.get("estimated_time") if isinstance(body, dict) else None
        if isinstance(raw, int | float):
            estimate = float(raw)
    except ValueError:
        body = None
    wait = f" (model loading; estimated_time={estimate:.0f}s)" if estimate is not None else ""
    return (
        f"HuggingFace model is cold-starting{wait}; retry shortly. "
        f"upstream: {repr(body)[:_ERR_BODY_MAX]}"
    )


def _reject_non_audio(response: httpx.Response) -> None:
    """Raise if a 2xx body is a JSON error envelope rather than audio bytes.

    HF can return a JSON error with a ``200`` status; without this guard a JSON
    blob would be probed/persisted as if it were audio. The content-type is the
    reliable discriminator (audio responses carry ``audio/*``).
    """
    content_type = response.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        raise HuggingFaceTtsError(
            "HuggingFace returned a JSON body where audio bytes were expected: "
            f"{response.text[:_ERR_BODY_MAX]}"
        )
