"""Composition root: assemble the workflow's collaborators from `Settings`.

This is the single place that wires the concrete collaborators (agents +
services) of both the Deep Research workflow (`ResearchDeps`) and the Media
Production layer (`MediaDeps`) together from configuration, mirroring
`app.services.llm.factory.build_router_from_settings` for the model fabric. The
workflow nodes / media pipeline receive everything pre-built via factory-closure
DI (ADR 0004, 0009); this module is where those concrete instances are minted.

Pure wiring: deliberately **no FastAPI import** so the boundary stays clean
(CLAUDE.md §10) — the thin request-time `Depends` providers live in
`app.api.deps` and just call into here.

Live provider selection (ADR 0032)
-----------------------------------
The composition root now wires *real* providers selected by config, instead of
raising. Selection is config-driven and provider-neutral (CLAUDE.md §6):

* **Model.** ``default_provider`` chooses the LLM adapter: ``openai-compatible``
  (base_url + api_key), ``gemini`` (native structured output), or any named
  preset in `app.services.llm.providers` (``groq``/``nvidia``/``huggingface``/
  ``ollama``). The provider is registered under the *config* name so the default
  policy — which keys every role by ``default_provider`` — resolves (a registry
  preset's adapter ``.name`` is always ``"openai-compatible"``, which would not
  match a policy keyed by ``"groq"``).
* **Search.** ``search_provider`` chooses the `SearchProvider` adapter:
  ``tavily`` (``search_api_key``) or ``brave`` (``brave_api_key``).

A missing key or an unknown name surfaces as a loud `CompositionError` at
wiring time — never a silent `Fake*` leaking into a running service. Tests
bypass this entirely by constructing fake-backed bundles and overriding the
`Depends` provider.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx

from app.agents.creator_packet import CreatorPacketAgent
from app.agents.cross_verification import CrossVerificationAgent
from app.agents.editorial_critic import EditorialCriticAgent
from app.agents.evidence_extraction import EvidenceExtractionAgent
from app.agents.report import ReportAgent
from app.agents.research_planner import ResearchPlannerAgent
from app.agents.source_discovery import SourceDiscoveryAgent
from app.agents.synthesis import SynthesisAgent
from app.core.config import Settings, get_settings
from app.core.lifecycle import AsyncClosable
from app.media.composition.base import CompositionService
from app.media.composition.ffmpeg import FfmpegCompositionService
from app.media.tts.base import TTSProvider
from app.media.tts.http_tts import HttpTtsProvider
from app.media.visuals.base import VisualProvider
from app.media.visuals.stock import StockVisualProvider
from app.services.ingestion.httpx_fetch import HttpxFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.base import ModelProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.policy import default_policy
from app.services.llm.providers import (
    PROVIDER_REGISTRY,
    MissingProviderKeyError,
    UnknownProviderPresetError,
    build_provider,
)
from app.services.llm.router import ModelRouter
from app.services.search.base import SearchProvider
from app.services.search.brave_search import BraveSearchProvider
from app.services.search.live import TavilySearchProvider
from app.workflows.deep_research import ResearchDeps


class CompositionError(RuntimeError):
    """A required collaborator could not be assembled from the current settings.

    Raised at composition time (not import time) so the app can boot and tests
    can override the dependency before the first request reaches the workflow.
    Normalizes every wiring failure to one type, so the API seam can map it to a
    single HTTP status (503; see `app.api.research.composition_error_handler`).
    """


def _unique_token(prefix: str) -> str:
    """A unique filename token for a written media artifact (storage, not a DTO).

    These name *files on disk* (the audio + fetched-visual blobs the live seams
    write), not media-layer DTO ids — so this is a tiny local helper, not a
    cross-layer import of the media schema's private ``_gen_id`` (which the media
    layer documents as copy-not-import). Same 64-bit-hex scheme for consistency.
    """
    return f"{prefix}_{secrets.token_hex(8)}"


# --- Model provider selection (ADR 0032) ------------------------------------


def _build_model_provider(settings: Settings) -> ModelProvider:
    """Build the configured LLM `ModelProvider`, or fail loud.

    Dispatches on ``default_provider``:

    * ``openai-compatible`` — the generic adapter over ``base_url`` + ``api_key``.
    * ``gemini`` — the native Gemini adapter (server-side structured output).
    * a name in `PROVIDER_REGISTRY` (``groq``/``nvidia``/``huggingface``/
      ``ollama``) — reuses `app.services.llm.providers.build_provider`, which owns
      each preset's ``base_url`` (we do not re-implement the registry here).

    Any unknown name or missing key surfaces as `CompositionError` rather than an
    opaque 401/404 at the first model call.
    """
    name = settings.default_provider
    if name == OpenAICompatibleProvider.name:
        if not settings.base_url:
            raise CompositionError(
                "default_provider='openai-compatible' requires REEL_AUTOMATION_BASE_URL"
            )
        if not settings.api_key.get_secret_value():
            raise CompositionError(
                "default_provider='openai-compatible' requires REEL_AUTOMATION_API_KEY"
            )
        return OpenAICompatibleProvider(
            base_url=settings.base_url,
            api_key=settings.api_key.get_secret_value(),
        )
    if name == GeminiProvider.name:
        if not settings.gemini_api_key.get_secret_value():
            raise CompositionError(
                "default_provider='gemini' requires REEL_AUTOMATION_GEMINI_API_KEY"
            )
        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            base_url=settings.gemini_base_url,
        )
    if name in PROVIDER_REGISTRY:
        try:
            return build_provider(name, settings)
        except (UnknownProviderPresetError, MissingProviderKeyError) as exc:
            raise CompositionError(str(exc)) from exc
    known = ", ".join(
        sorted({OpenAICompatibleProvider.name, GeminiProvider.name, *PROVIDER_REGISTRY})
    )
    raise CompositionError(
        f"no model provider adapter for default_provider={name!r}; set "
        f"REEL_AUTOMATION_DEFAULT_PROVIDER to one of: {known}"
    )


def _build_router(settings: Settings) -> tuple[ModelRouter, ModelProvider]:
    """Build a `ModelRouter` + return its underlying provider for lifecycle.

    The provider is registered under the **config name** (``default_provider``),
    not the adapter's own ``.name``: the default policy keys every role by
    ``default_provider``, and a registry preset's adapter is always named
    ``"openai-compatible"`` regardless of the preset (``groq`` etc.). Registering
    under the config name is what makes role resolution succeed for every
    selectable backend.

    The provider is also returned so the composition root can close its
    httpx client on shutdown (ADR 0044) — the router itself is not closable.
    """
    provider = _build_model_provider(settings)
    router = ModelRouter(
        providers={settings.default_provider: provider},
        policy=default_policy(settings),
    )
    return router, provider


# --- Search provider selection (ADR 0032) -----------------------------------


def _build_search_provider(settings: Settings) -> SearchProvider:
    """Build the configured `SearchProvider`, or fail loud if its key is unset.

    ``search_provider`` selects the adapter; each reads its own key so search and
    the model are configured independently. A test double is never returned — an
    unconfigured search backend raises `CompositionError`.
    """
    name = settings.search_provider
    if name == TavilySearchProvider.name:
        key = settings.search_api_key.get_secret_value()
        if not key:
            raise CompositionError(
                "search_provider='tavily' requires REEL_AUTOMATION_SEARCH_API_KEY"
            )
        return TavilySearchProvider(api_key=key)
    if name == BraveSearchProvider.name:
        key = settings.brave_api_key.get_secret_value()
        if not key:
            raise CompositionError("search_provider='brave' requires REEL_AUTOMATION_BRAVE_API_KEY")
        return BraveSearchProvider(api_key=key)
    known = ", ".join(sorted({TavilySearchProvider.name, BraveSearchProvider.name}))
    raise CompositionError(
        f"no search provider adapter for search_provider={name!r}; set "
        f"REEL_AUTOMATION_SEARCH_PROVIDER to one of: {known}"
    )


@dataclass(frozen=True)
class ResearchBundle:
    """A built `ResearchDeps` plus the httpx-owning providers to close on shutdown.

    The composition root is the only place that knows which concrete adapters were
    minted (the agents wrap them privately), so it returns the `AsyncClosable`
    seams alongside the deps rather than have the API layer reach through agent
    internals to find their clients (ADR 0044; preserves the agent/tool boundary,
    CLAUDE.md §4/§10). The app lifespan drains ``closables`` on shutdown.
    """

    deps: ResearchDeps
    closables: tuple[AsyncClosable, ...]


def build_research_deps(settings: Settings | None = None) -> ResearchBundle:
    """Assemble the workflow's `ResearchDeps` bundle (+ its closables) from settings.

    All LLM-backed agents share one `ModelRouter` (built from the configured
    provider + role policy); the discovery agent additionally needs a
    `SearchProvider` and ingestion needs a `FetchProvider`. Each of those three
    httpx-owning seams is returned as an `AsyncClosable` so the app can close its
    client on shutdown (ADR 0044). An unconfigured model or search backend
    surfaces as a loud `CompositionError` — see the module docstring.
    """
    resolved = settings or get_settings()
    router, model_provider = _build_router(resolved)
    search = _build_search_provider(resolved)
    fetch = HttpxFetchProvider()
    deps = ResearchDeps(
        planner=ResearchPlannerAgent(router),
        discovery=SourceDiscoveryAgent(router, search),
        ingestion=IngestionService(fetch),
        extractor=EvidenceExtractionAgent(router),
        verifier=CrossVerificationAgent(router),
        synthesizer=SynthesisAgent(router),
        critic=EditorialCriticAgent(router),
        reporter=ReportAgent(router),
        strategist=CreatorPacketAgent(router),
    )
    # The live model/search adapters here are all `CloseOwnedClientMixin`
    # subclasses (only the Fake* test doubles, never built by this root, are not).
    # The `ModelProvider`/`SearchProvider` protocols don't declare `aclose` (the
    # fakes have no client to close), so narrow to `AsyncClosable` for the bundle.
    closables: tuple[AsyncClosable, ...] = (
        cast(AsyncClosable, model_provider),
        cast(AsyncClosable, search),
        fetch,
    )
    return ResearchBundle(deps=deps, closables=closables)


# --- Media production deps (ADR 0032) ---------------------------------------

#: Bridges a *remote* visual uri (e.g. a Pexels ``https://`` link) to a local
#: ``file://`` uri the ffmpeg adapter can resolve. The `StockVisualProvider`
#: mints remote https uris, but `FfmpegCompositionService.resolve_local_path`
#: accepts only ``file://``/bare paths — so without this bridge a live render
#: with stock B-roll would raise. Injected (mirroring the TTS `AudioSink`) so the
#: pipeline stays storage-neutral; ``None`` means "pass uris through unchanged"
#: (the fake path, where uris are already local/synthetic).
VisualSink = Callable[[str], str]


@dataclass(frozen=True)
class MediaDeps:
    """The deterministic media seams the `VideoPipeline` depends on, as one bundle.

    Mirrors `ResearchDeps`: a single typed container of pre-built collaborators
    injected into the pipeline, rather than a growing kwarg list. ``visuals`` is
    optional — composition tolerates an empty visual list with the fake renderer,
    but the live ffmpeg renderer **requires** at least one visual, so a live media
    build wires a `VisualProvider`. ``visual_sink`` bridges that provider's
    *remote* uris to local ``file://`` uris ffmpeg can resolve (see `VisualSink`);
    it is ``None`` in the fake path (uris are already local/synthetic). The
    `SubtitleService` is intentionally *not* in this bundle: it is pure, hermetic
    shipping code that `MediaPipeline` defaults to internally
    (`DeterministicSubtitleService`), exactly as ingestion defaults its PDF parser.
    """

    tts: TTSProvider
    composition: CompositionService
    visuals: VisualProvider | None = None
    visual_sink: VisualSink | None = None
    voice: str = "narrator"


@dataclass(frozen=True)
class MediaBundle:
    """A built `MediaDeps` plus the httpx-owning media providers to close on shutdown.

    The media-band analogue of `ResearchBundle` (ADR 0044): the TTS provider — and
    the stock visual provider when configured — own httpx clients that the app
    closes on shutdown. ``FfmpegCompositionService`` shells out to a binary and
    owns no client, so it is not a closable.
    """

    deps: MediaDeps
    closables: tuple[AsyncClosable, ...]


def build_media_deps(settings: Settings | None = None) -> MediaBundle:
    """Assemble the live `MediaDeps` bundle (+ its closables) from settings (ADR 0032).

    Wires the real media seams for an end-to-end render:

    * `HttpTtsProvider` over the configured ``tts_base_url`` + ``tts_api_key``,
      with a **filesystem sink** that writes the synthesized audio under
      ``media_output_dir`` and returns a ``file://`` URI — the scheme the ffmpeg
      adapter can resolve (it rejects ``http``/``fake`` schemes).
    * `FfmpegCompositionService` writing into ``media_output_dir`` (ADR 0023).
    * `StockVisualProvider` (ADR 0024) **and** a `visual_sink` when
      ``stock_api_key`` is set: the provider mints remote ``https`` B-roll uris,
      and the sink fetches each to a local file under ``media_output_dir`` and
      returns its ``file://`` uri — the bridge ffmpeg needs (without it the
      remote uri would raise in `resolve_local_path`). Both are ``None`` when no
      stock key is set; a live render then has no visual and fails loudly in
      ffmpeg (the honest behavior, not a silent default).

    A missing TTS endpoint/key surfaces as `CompositionError`. This live path is
    config + ffmpeg-binary + network gated; the hermetic tests inject a
    fake-backed `MediaDeps` directly and never reach here.
    """
    resolved = settings or get_settings()

    if not resolved.tts_base_url:
        raise CompositionError("media render requires REEL_AUTOMATION_TTS_BASE_URL")
    tts_key = resolved.tts_api_key.get_secret_value()
    if not tts_key:
        raise CompositionError("media render requires REEL_AUTOMATION_TTS_API_KEY")

    output_dir = Path(resolved.media_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _audio_sink(audio_bytes: bytes) -> str:
        # Persist the opaque audio blob next to the rendered video and return a
        # file:// URI the ffmpeg adapter can resolve (ADR 0019 storage seam: the
        # adapter is storage-neutral; this composition root chooses the filesystem).
        path = output_dir / f"{_unique_token('aud')}.audio"
        path.write_bytes(audio_bytes)
        return path.resolve().as_uri()

    tts = HttpTtsProvider(base_url=resolved.tts_base_url, api_key=tts_key, sink=_audio_sink)
    composition = FfmpegCompositionService(output_dir=output_dir)

    visuals: VisualProvider | None = None
    visual_sink: VisualSink | None = None
    closables: list[AsyncClosable] = [tts]
    if resolved.stock_api_key.get_secret_value():
        stock = StockVisualProvider(api_key=resolved.stock_api_key.get_secret_value())
        visuals = stock
        visual_sink = _make_filesystem_visual_sink(output_dir)
        closables.append(stock)

    deps = MediaDeps(
        tts=tts,
        composition=composition,
        visuals=visuals,
        visual_sink=visual_sink,
        voice=resolved.tts_voice,
    )
    return MediaBundle(deps=deps, closables=tuple(closables))


def _make_filesystem_visual_sink(output_dir: Path) -> VisualSink:
    """Build a `VisualSink` that downloads a remote uri to a local ``file://`` one.

    The visual analogue of the TTS audio sink: fetches the asset bytes with a
    bounded-timeout ``httpx`` GET, writes them under ``output_dir``, and returns
    the local ``file://`` uri. A bare/``file://`` uri is returned unchanged (no
    fetch — it is already local). Network failures propagate as ``httpx`` errors
    for the caller to handle, mirroring the adapters' error boundary (ADR 0007).
    """

    def _sink(uri: str) -> str:
        if uri.startswith("file://") or "://" not in uri:
            return uri  # already a local path / file uri — nothing to fetch
        response = httpx.get(uri, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        path = output_dir / f"{_unique_token('vis')}.media"
        path.write_bytes(response.content)
        return path.resolve().as_uri()

    return _sink
