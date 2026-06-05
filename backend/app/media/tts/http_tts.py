"""Provider-neutral HTTP ``TTSProvider`` adapter (httpx-based).

Speaks a *generic* REST text-to-speech contract, so a single adapter serves any
backend exposing it — selected entirely by ``base_url`` + ``api_key`` + the audio
``sink`` (configuration, no code change; CLAUDE.md §6 provider abstraction).

Built on ``httpx`` (already a runtime dependency) so request building and the
bytes → ``SynthesizedSpeech`` mapping are unit-testable **offline** via
``httpx.MockTransport``; only the live call needs network. This mirrors the LLM
adapter's hardening (`app.services.llm.openai_compatible`): bounded timeout, the
API key passed in at construction (never read from global ``Settings``, so this
file stays out of ``config.py``), and ``raise_for_status`` on transport errors.
See ADR 0022.

The generic REST contract this adapter targets:

- ``POST {base_url}/synthesize`` with JSON body ``{"text": ..., "voice": ...}``
  and an ``Authorization: Bearer {api_key}`` header.
- The response body is the **raw audio bytes** (the ``TTSProvider`` is, per
  CLAUDE.md §4, a deterministic tool: text in, audio out).
- The clip duration is returned in the ``X-Audio-Duration-Ms`` response header
  (an integer count of milliseconds). ``SynthesizedSpeech.duration_ms`` is a
  required, ``ge=0`` field that cannot be recovered from opaque audio bytes
  without a format-specific parser (which would add a dependency and break
  provider-neutrality), so the header is part of the contract; a missing or
  malformed header is an error rather than a silently-wrong ``0``.

Storage seam: the audio bytes are an opaque blob *owned by storage*; the media
layer traffics in descriptors (ADR 0019 / ``SynthesizedSpeech`` docstring). So
this adapter does not choose where audio lives — a ``sink`` callable is injected
at construction (mirroring the injected ``client``): it persists the bytes and
returns the ``audio_uri`` to record. Tests inject a capturing in-memory sink; a
real deployment injects an object-store / filesystem sink.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.media.schemas import SynthesizedSpeech

PROVIDER_NAME = "http"

#: Persists synthesized audio bytes and returns the ``audio_uri`` they live at.
#: Injected at construction so the adapter stays storage-neutral (ADR 0019).
AudioSink = Callable[[bytes], str]

#: Response header carrying the clip duration in integer milliseconds.
DURATION_HEADER = "X-Audio-Duration-Ms"


class HttpTtsError(RuntimeError):
    """Raised on a malformed / contract-violating TTS response.

    Transport-level failures surface as their native ``httpx`` exceptions
    (e.g. ``httpx.HTTPStatusError`` from ``raise_for_status``), mirroring the
    LLM adapter; this class covers the response *shape* failures specific to the
    TTS contract (e.g. a missing duration header).
    """


class HttpTtsProvider:
    """A ``TTSProvider`` over a generic REST TTS endpoint."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        sink: AudioSink,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not base_url:
            raise HttpTtsError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._sink = sink
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        response = await self._client.post(
            f"{self._base_url}/synthesize",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"text": text, "voice": voice},
        )
        response.raise_for_status()

        duration_ms = _parse_duration_ms(response.headers.get(DURATION_HEADER))
        audio_uri = self._sink(response.content)

        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )


def _parse_duration_ms(raw: str | None) -> int:
    """Parse the duration header into a non-negative int, or raise.

    Avoids a silently-wrong ``0`` when the backend omits/garbles the header:
    ``duration_ms`` drives downstream composition timing, so a bad value must
    fail loud rather than propagate.
    """
    if raw is None:
        raise HttpTtsError(f"TTS response missing required {DURATION_HEADER!r} header")
    try:
        value = int(raw)
    except ValueError as exc:
        raise HttpTtsError(
            f"TTS response {DURATION_HEADER!r} header is not an integer: {raw!r}"
        ) from exc
    if value < 0:
        raise HttpTtsError(f"TTS response {DURATION_HEADER!r} header is negative: {value}")
    return value
