"""NVIDIA NIM ``TTSProvider`` adapter over a hosted TTS speech endpoint (httpx).

Targets the operator's existing NVIDIA build / NIM key. The same key already
drives the repo's LLM path through the OpenAI-compatible adapter
(`app.services.llm.openai_compatible`) — build.nvidia.com's hosted catalog
speaks an OpenAI-compatible JSON + ``Authorization: Bearer`` wire shape — so this
adapter mirrors that posture: a single ``httpx`` adapter selected entirely by
``base_url`` + ``api_key`` + ``model`` + the audio ``sink`` (configuration, no
code change; CLAUDE.md §6 provider abstraction).

Why a dedicated NVIDIA adapter rather than the generic ``http_tts.py``: that one
requires an ``X-Audio-Duration-Ms`` response header, which a real NVIDIA speech
endpoint does not send — it returns **only the raw audio bytes**. This adapter
recovers the duration from the *rendered audio itself* with ``ffprobe`` (the
probe twin of the ``ffmpeg`` binary the composition layer already requires;
ADR 0023), so it works out-of-box.

The wire contract for NVIDIA's speech NIM is **not** firmly pinned to OpenAI's
``/audio/speech`` (its native surfaces are gRPC / WebSocket, and the self-hosted
REST tutorial uses a multipart ``/v1/audio/synthesize`` form), so the request
shape is isolated here behind named constants and a single ``_build_payload`` so
the first live call can confirm/adjust it with a small edit rather than a
rewrite. See the **wire-contract assumption** below and ADR 0047.

Built on ``httpx`` (already a runtime dependency), so request building and the
bytes → ``SynthesizedSpeech`` mapping are unit-testable **offline** via
``httpx.MockTransport``. Hardening mirrors the LLM adapter
(`app.services.llm.openai_compatible`): bounded timeout, the API key passed in at
construction (never read from global ``Settings``, so this file stays out of
``config.py``), ``raise_for_status`` on transport errors, and the key never
appearing in a log/repr/error.

Wire-contract assumption (verify on the first live call) — modeled on the
OpenAI-compatible speech shape build.nvidia.com proxies:

- ``POST {base_url}/audio/speech`` with JSON body
  ``{"model": ..., "input": <text>, "voice": ..., "response_format": ...}`` and
  an ``Authorization: Bearer {api_key}`` header. The protocol's ``synthesize``
  takes only ``text`` + ``voice`` (the deterministic-tool contract; CLAUDE.md
  §4), so ``model`` and ``response_format`` are fixed at construction.
- The response body is the **raw audio bytes** in ``response_format``.
- There is **no** duration in the response — it is computed from the written file
  with ``ffprobe`` (see below).

If the live endpoint instead wants the NIM-native shape (e.g. an ``input.text``
field, a ``language_code``, or a multipart ``/v1/audio/synthesize`` form), only
the constants + ``_build_payload`` / request line here change; the sink, probe,
and descriptor mapping are unaffected.

Duration probe — the load-bearing split (mirrors ``ffmpeg.py`` / ADR 0023):

* Command construction (`build_ffprobe_args`) and output parsing
  (`parse_ffprobe_duration_ms`) are pure, deterministic functions — assertable
  with **no ``ffprobe`` binary present**.
* Execution (`_probe_duration_ms`) is the single ``subprocess.run`` seam, run off
  the event loop via ``asyncio.to_thread`` and a mockable point in tests. A
  missing binary or a malformed probe both surface as a clear `NvidiaTtsError`
  rather than a silently-wrong ``0`` — ``duration_ms`` drives downstream
  composition timing (the "video as long as its narration" rule), so a bad value
  must fail loud.

Storage seam: the audio bytes are an opaque blob *owned by storage*; the media
layer traffics in descriptors (ADR 0019 / ``SynthesizedSpeech`` docstring). A
``sink`` callable is injected at construction (mirroring ``http_tts.py``): it
persists the bytes and returns the ``audio_uri`` to record. Because the duration
is probed from the *written file*, the sink must return a ``file://`` URI or a
bare local path (the local render pipeline requires this of its audio anyway);
the URI is resolved to a path by the composition layer's `resolve_local_path`
(reused, not reimplemented), which fails loud on a non-resolvable scheme (e.g. an
in-memory ``mem://`` sink).
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

PROVIDER_NAME = "nvidia"

#: Persists synthesized audio bytes and returns the ``audio_uri`` they live at.
#: Injected at construction so the adapter stays storage-neutral (ADR 0019).
#: Must return a ``file://`` URI or bare local path so the duration probe can
#: resolve the written file (see module docstring).
AudioSink = Callable[[bytes], str]

#: Request path for the hosted speech endpoint (joined to ``base_url``). Isolated
#: here so the wire-contract assumption can be adjusted in one place after the
#: first live call (see module docstring).
SPEECH_PATH = "/audio/speech"

#: Default NVIDIA TTS NIM model id. ``model`` is required by the assumed contract
#: and is overridable at construction; this default mirrors a build.nvidia.com
#: TTS model slug and should be confirmed against the live catalog.
DEFAULT_MODEL = "magpie-tts-multilingual"

#: Default audio container; mp3 is broadly supported by ``ffprobe`` and ffmpeg.
DEFAULT_RESPONSE_FORMAT = "mp3"

# How many trailing characters of ffprobe's stderr to surface in an error.
_STDERR_TAIL = 2000


class NvidiaTtsError(RuntimeError):
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
        raise NvidiaTtsError(f"ffprobe output did not contain format.duration: {stdout!r}") from exc
    try:
        seconds = float(raw)
    except (ValueError, TypeError) as exc:
        raise NvidiaTtsError(f"ffprobe format.duration is not a number: {raw!r}") from exc
    if seconds < 0:
        raise NvidiaTtsError(f"ffprobe format.duration is negative: {seconds}")
    return round(seconds * 1000)


class NvidiaTtsProvider:
    """A ``TTSProvider`` over an NVIDIA TTS NIM speech endpoint."""

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
            raise NvidiaTtsError("base_url is required")
        if not model:
            raise NvidiaTtsError("model is required (the NVIDIA TTS NIM requires a model)")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._sink = sink
        self._model = model
        self._response_format = response_format
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._probe_timeout = probe_timeout

    def _build_payload(self, *, text: str, voice: str) -> dict[str, str]:
        """Build the JSON request body. Pure — isolates the wire-contract assumption.

        Kept as a single seam so confirming/adjusting the NVIDIA NIM body shape on
        the first live call (e.g. a nested ``input``, a ``language_code``) is a
        one-method edit rather than a change scattered through ``synthesize``.
        """
        return {
            "model": self._model,
            "input": text,
            "voice": voice,
            "response_format": self._response_format,
        }

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        response = await self._client.post(
            f"{self._base_url}{SPEECH_PATH}",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=self._build_payload(text=text, voice=voice),
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
        """Run ``ffprobe`` on the file; normalize failures to `NvidiaTtsError`.

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
            raise NvidiaTtsError(
                f"ffprobe binary not found (is ffmpeg installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NvidiaTtsError(
                f"ffprobe timed out after {self._probe_timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise NvidiaTtsError(
                f"ffprobe exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return parse_ffprobe_duration_ms(result.stdout.decode("utf-8", errors="replace"))
