"""OpenAI-compatible ``TTSProvider`` adapter over ``POST /audio/speech`` (httpx).

Speaks the OpenAI ``/v1/audio/speech`` API, so a *single* adapter serves any
compatible backend — OpenAI itself or any drop-in exposing the same shape —
selected entirely by ``base_url`` + ``api_key`` + ``model`` + the audio ``sink``
(configuration, no code change; CLAUDE.md §6 provider abstraction).

Why this exists alongside ``http_tts.py``: the generic ``HttpTtsProvider``
requires an ``X-Audio-Duration-Ms`` response header to populate
``SynthesizedSpeech.duration_ms``. Real ``/audio/speech`` endpoints return **only
the raw audio bytes**, no duration header — so that adapter fails against any
standard provider. This adapter recovers the duration from the *rendered audio
itself* with ``ffprobe`` (the probe twin of the ``ffmpeg`` binary the composition
layer already requires; ADR 0023), so it works out-of-box.

Built on ``httpx`` (already a runtime dependency), so request building and the
bytes → ``SynthesizedSpeech`` mapping are unit-testable **offline** via
``httpx.MockTransport``. This mirrors the LLM adapter's hardening
(`app.services.llm.openai_compatible`): bounded timeout, the API key passed in at
construction (never read from global ``Settings``, so this file stays out of
``config.py``), and ``raise_for_status`` on transport errors. See ADR 0045.

The OpenAI ``/audio/speech`` contract this adapter targets:

- ``POST {base_url}/audio/speech`` with JSON body
  ``{"model": ..., "input": <text>, "voice": ..., "response_format": ...}`` and
  an ``Authorization: Bearer {api_key}`` header. The protocol's ``synthesize``
  takes only ``text`` + ``voice`` (the deterministic-tool contract; CLAUDE.md
  §4), so ``model`` (required by OpenAI) and ``response_format`` are fixed at
  construction.
- The response body is the **raw audio bytes** in ``response_format``.
- There is **no** duration in the response — it is computed below from the
  written file with ``ffprobe`` rather than trusted from an absent header.

Duration probe — the load-bearing split (mirrors ``ffmpeg.py`` / ADR 0023):

* Command construction (`build_ffprobe_args`) and output parsing
  (`parse_ffprobe_duration_ms`) are pure, deterministic functions — assertable
  with **no ``ffprobe`` binary present**.
* Execution (`_probe_duration_ms`) is the single ``subprocess.run`` seam, run off
  the event loop via ``asyncio.to_thread`` and a mockable point in tests. A
  missing binary or a malformed probe both surface as a clear `OpenAiTtsError`
  rather than a silently-wrong ``0`` — ``duration_ms`` drives downstream
  composition timing (the "video as long as its narration" rule), so a bad value
  must fail loud.

Storage seam: the audio bytes are an opaque blob *owned by storage*; the media
layer traffics in descriptors (ADR 0019 / ``SynthesizedSpeech`` docstring). A
``sink`` callable is injected at construction (mirroring ``http_tts.py``): it
persists the bytes and returns the ``audio_uri`` to record. Because the duration
is probed from the *written file*, this adapter requires a sink that returns a
``file://`` URI or a bare local path (the local render pipeline requires this of
its audio anyway) — the URI is resolved to a path by the composition layer's
`resolve_local_path` (reused, not reimplemented), which fails loud on a
non-resolvable scheme (e.g. an in-memory ``mem://`` sink).
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from app.media.composition.ffmpeg import resolve_local_path
from app.media.schemas import SynthesizedSpeech

PROVIDER_NAME = "openai"

#: Persists synthesized audio bytes and returns the ``audio_uri`` they live at.
#: Injected at construction so the adapter stays storage-neutral (ADR 0019).
#: Must return a ``file://`` URI or bare local path so the duration probe can
#: resolve the written file (see module docstring).
AudioSink = Callable[[bytes], str]

#: Default OpenAI TTS model (``model`` is required by ``/audio/speech``).
DEFAULT_MODEL = "tts-1"

#: Default audio container; mp3 is broadly supported by ``ffprobe`` and ffmpeg.
DEFAULT_RESPONSE_FORMAT = "mp3"

# How many trailing characters of ffprobe's stderr to surface in an error.
_STDERR_TAIL = 2000


class OpenAiTtsError(RuntimeError):
    """Raised on a contract-violating TTS response or a failed duration probe.

    Transport-level failures surface as their native ``httpx`` exceptions
    (e.g. ``httpx.HTTPStatusError`` from ``raise_for_status``), mirroring the
    LLM adapter; this class covers the response *shape* / probe failures specific
    to this adapter (e.g. ``ffprobe`` missing or returning no duration).
    """


def build_ffprobe_args(audio_path: Path) -> list[str]:
    """Build the ``ffprobe`` argv that prints a file's duration as JSON. Pure.

    Deterministic: given the same path it returns the same argv, every token
    explicit and assertable — it touches no I/O and mints no ids (ADR 0023). The
    output is the container's ``format.duration`` (seconds, as a string) in JSON,
    which `parse_ffprobe_duration_ms` consumes.
    """
    return [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(audio_path),
    ]


def parse_ffprobe_duration_ms(stdout: str) -> int:
    """Parse ``ffprobe`` JSON output into a non-negative duration in ms, or raise.

    Pure and deterministic. Avoids a silently-wrong ``0`` when ffprobe cannot
    determine the duration (e.g. ``"N/A"`` or a missing key): ``duration_ms``
    drives downstream composition timing, so a bad value must fail loud.
    """
    try:
        data = json.loads(stdout)
        raw = data["format"]["duration"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OpenAiTtsError(f"ffprobe output did not contain format.duration: {stdout!r}") from exc
    try:
        seconds = float(raw)
    except (ValueError, TypeError) as exc:
        raise OpenAiTtsError(f"ffprobe format.duration is not a number: {raw!r}") from exc
    if seconds < 0:
        raise OpenAiTtsError(f"ffprobe format.duration is negative: {seconds}")
    return round(seconds * 1000)


class OpenAiTtsProvider:
    """A ``TTSProvider`` over the OpenAI ``/audio/speech`` API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        sink: AudioSink,
        model: str = DEFAULT_MODEL,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        probe_timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise OpenAiTtsError("base_url is required")
        if not model:
            raise OpenAiTtsError("model is required (/audio/speech requires a model)")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._sink = sink
        self._model = model
        self._response_format = response_format
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._probe_timeout = probe_timeout

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        response = await self._client.post(
            f"{self._base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "input": text,
                "voice": voice,
                "response_format": self._response_format,
            },
        )
        response.raise_for_status()

        # Persist the bytes (storage-neutral), then probe the *written file* for
        # its duration — the response carries none. The sink must return a
        # file://-or-bare path; resolve_local_path fails loud otherwise.
        audio_uri = self._sink(response.content)
        audio_path = resolve_local_path(audio_uri)
        duration_ms = await asyncio.to_thread(self._probe_duration_ms, audio_path)

        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )

    def _probe_duration_ms(self, audio_path: Path) -> int:
        """Run ``ffprobe`` on the file; normalize failures to `OpenAiTtsError`.

        The single subprocess seam (a mockable point). Uses the argv **list** —
        never a shell string — so there is no injection surface; ``shlex.join``
        renders a human-readable command only in error messages. A missing binary
        and a non-zero exit both fail loud.
        """
        args = build_ffprobe_args(audio_path)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                timeout=self._probe_timeout,
            )
        except FileNotFoundError as exc:
            raise OpenAiTtsError(
                f"ffprobe binary not found (is ffmpeg installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenAiTtsError(
                f"ffprobe timed out after {self._probe_timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise OpenAiTtsError(
                f"ffprobe exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return parse_ffprobe_duration_ms(result.stdout.decode("utf-8", errors="replace"))
