"""TTS fabric: a deterministic router over named `TTSProvider` backends.

This is the media-layer analogue of the LLM model fabric's router +
resilience-fallback (CLAUDE.md §3.3, §4, §6): pure, provider-neutral selection
and fault-tolerance machinery — a *tool/service*, never an agent. It holds named
backends and an ordered fallback policy (prefer the local/cheapest, e.g. kokoro,
then nvidia, then huggingface) and *guarantees delivery*: `synthesize` tries the
chosen backend and, on failure, walks the rest of the fallback order until one
succeeds, raising only when every backend fails.

Two deliberate differences from `app.services.llm.resilience.complete_with_fallback`,
which it otherwise mirrors:

- That helper does exactly **one** hop (primary -> ``FALLBACK`` role). This router
  does a **full ordered traversal** — the chosen backend, then every remaining
  backend in policy order — because a media render should produce *some* audio if
  any backend can, even at degraded quality.
- The already-tried chosen backend is skipped during traversal (never tried
  twice), so a mid-order choice still gets the cheaper earlier backends as a
  safety net.

The *judgment* half — *which* backend/voice best suits a script — lives in the
`TTSSupervisorAgent` (CLAUDE.md §4). This router only executes + falls back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider


class TTSRoutingError(RuntimeError):
    """Base class for TTS routing configuration/usage errors."""


class UnknownBackendError(TTSRoutingError):
    """Raised when a named backend is not registered with the router."""


class TTSExhaustedError(TTSRoutingError):
    """Raised when every backend in the fallback chain failed to synthesize.

    Chains the *last* underlying provider failure (``__cause__``) so the caller
    sees the real synthesis error, not just a synthetic wrapper.
    """


class TTSRouter:
    """Routes synthesis to named backends with deterministic ordered fallback.

    Holds a registry of named `TTSProvider`s and an ordered ``fallback_order``
    policy (cheapest/most-local first). `synthesize` starts at the chosen backend
    (or the policy default) and, on failure, hops to the next backend in policy
    order until one succeeds. Selection + fallback are pure and hermetic; no
    network or audio is touched here (the providers do that).
    """

    def __init__(
        self,
        providers: Mapping[str, TTSProvider],
        fallback_order: Sequence[str],
    ) -> None:
        if not fallback_order:
            raise TTSRoutingError("fallback_order must list at least one backend")
        unknown = [name for name in fallback_order if name not in providers]
        if unknown:
            raise TTSRoutingError(f"fallback_order names unregistered backend(s): {unknown!r}")
        self._providers: dict[str, TTSProvider] = dict(providers)
        self._fallback_order: tuple[str, ...] = tuple(fallback_order)

    @property
    def default_backend(self) -> str:
        """The first (preferred/cheapest) backend in the fallback policy."""
        return self._fallback_order[0]

    def available(self) -> frozenset[str]:
        """The set of registered backend names (the supervisor's valid choices)."""
        return frozenset(self._providers)

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        backend: str | None = None,
    ) -> SynthesizedSpeech:
        """Synthesize ``text`` in ``voice``, starting at ``backend``, with fallback.

        Tries ``backend`` (defaulting to `default_backend`) first, then walks the
        rest of the fallback order — skipping the already-tried chosen backend —
        until one succeeds. Returns the first success.

        Raises `UnknownBackendError` if ``backend`` is named but not registered,
        or `TTSExhaustedError` (chaining the last provider failure) if every
        backend in the chain raises.
        """
        if backend is not None and backend not in self._providers:
            raise UnknownBackendError(f"unknown backend {backend!r}")
        chosen = backend or self.default_backend

        chain = self._chain(chosen)
        last_error: Exception | None = None
        for name in chain:
            try:
                return await self._providers[name].synthesize(text=text, voice=voice)
            except Exception as exc:  # any provider failure triggers fallback to next
                last_error = exc
        raise TTSExhaustedError(f"all TTS backends failed (tried {list(chain)})") from last_error

    def _chain(self, chosen: str) -> tuple[str, ...]:
        """The try order: the chosen backend, then the rest of the policy order.

        The chosen backend is removed from its policy position so it is never
        tried twice; the remaining backends keep their cheapest-first order.
        """
        rest = tuple(name for name in self._fallback_order if name != chosen)
        return (chosen, *rest)
