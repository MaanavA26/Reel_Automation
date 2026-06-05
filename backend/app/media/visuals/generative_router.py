"""Config-driven selector for the generative-video backend (single pick, no fallback).

The generative-video analogue of the composition root's ``_build_*`` dispatch — a
*tool/service*, never an agent. It reads `Settings`, builds the **one** configured
`GenerativeVisualProvider`, and passes the vendor's secrets in at construction
(the adapters never read global `Settings`; this factory is the only seam that
does — mirroring `_build_search_provider` / `_build_tts_provider`).

Deliberately **not** a fallback router like `TTSRouter`: TTS falls back across
local/cheap backends because a render must produce *some* audio. Generative video
is paid and minutes-long per call, so silently retrying a *different* vendor on
failure multiplies cost + latency for no clear benefit — the strategist picks one
backend by config. Selection is by ``settings.generative_video_backend``:

* empty (the default) -> ``None`` (the feature is off, mirroring how the stock
  visual provider is ``None`` when no key is set — a live render then has no
  generative source and the existing retrieval/ffmpeg path is unaffected).
* a known name with its credentials present -> that adapter.
* a known name with credentials missing, or an unknown name -> a loud
  `GenerativeRoutingError` (fail loud, no silent default; CLAUDE.md §11).

This builds capability + selection; wiring a generated clip into the render path
is a documented follow-up (capability-before-wiring; ADR 0047/0053).
"""

from __future__ import annotations

from app.core.config import Settings
from app.media.visuals.generative import GenerativeVisualProvider
from app.media.visuals.generative_providers.kling import KlingGenerativeProvider
from app.media.visuals.generative_providers.luma import LumaGenerativeProvider
from app.media.visuals.generative_providers.pika import PikaGenerativeProvider
from app.media.visuals.generative_providers.runway import RunwayGenerativeProvider
from app.media.visuals.generative_providers.veo import VeoGenerativeProvider

#: The backend names this factory knows how to build (the operator's valid
#: choices for ``generative_video_backend``).
KNOWN_BACKENDS: frozenset[str] = frozenset({"veo", "runway", "luma", "pika", "kling"})


class GenerativeRoutingError(RuntimeError):
    """Raised when the configured generative-video backend cannot be built.

    Covers an unknown backend name and a known backend whose required credentials
    are absent — both fail loud rather than silently degrading.
    """


def build_generative_visual_provider(settings: Settings) -> GenerativeVisualProvider | None:
    """Build the configured `GenerativeVisualProvider`, or ``None`` if disabled.

    Returns ``None`` when ``generative_video_backend`` is empty (feature off).
    Otherwise dispatches on the name, reading the vendor's secrets from
    ``settings`` and passing them in at construction. Raises
    `GenerativeRoutingError` for an unknown name or missing credentials.
    """
    backend = settings.generative_video_backend.strip().lower()
    if not backend:
        return None
    if backend not in KNOWN_BACKENDS:
        raise GenerativeRoutingError(
            f"unknown generative_video_backend {backend!r} (known: {sorted(KNOWN_BACKENDS)})"
        )

    if backend == "runway":
        key = settings.runway_api_key.get_secret_value()
        if not key:
            raise GenerativeRoutingError(
                "generative_video_backend='runway' but REEL_AUTOMATION_RUNWAY_API_KEY is unset"
            )
        return RunwayGenerativeProvider(api_key=key)

    if backend == "luma":
        key = settings.luma_api_key.get_secret_value()
        if not key:
            raise GenerativeRoutingError(
                "generative_video_backend='luma' but REEL_AUTOMATION_LUMA_API_KEY is unset"
            )
        return LumaGenerativeProvider(api_key=key)

    if backend == "pika":
        key = settings.pika_fal_api_key.get_secret_value()
        if not key:
            raise GenerativeRoutingError(
                "generative_video_backend='pika' but REEL_AUTOMATION_PIKA_FAL_API_KEY is unset"
            )
        return PikaGenerativeProvider(api_key=key)

    if backend == "kling":
        ak = settings.kling_access_key.get_secret_value()
        sk = settings.kling_secret_key.get_secret_value()
        if not ak or not sk:
            raise GenerativeRoutingError(
                "generative_video_backend='kling' but REEL_AUTOMATION_KLING_ACCESS_KEY / "
                "REEL_AUTOMATION_KLING_SECRET_KEY is unset"
            )
        return KlingGenerativeProvider(access_key=ak, secret_key=sk)

    # backend == "veo"
    token = settings.veo_access_token.get_secret_value()
    if not token or not settings.veo_project or not settings.veo_storage_uri:
        raise GenerativeRoutingError(
            "generative_video_backend='veo' requires REEL_AUTOMATION_VEO_ACCESS_TOKEN, "
            "REEL_AUTOMATION_VEO_PROJECT, and REEL_AUTOMATION_VEO_STORAGE_URI"
        )
    return VeoGenerativeProvider(
        access_token=token,
        project=settings.veo_project,
        location=settings.veo_location,
        storage_uri=settings.veo_storage_uri,
    )
