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
from app.agents.tts_supervisor import TTSSupervisorAgent
from app.core.config import Settings, get_settings
from app.core.lifecycle import AsyncClosable
from app.media.composition.base import CompositionService
from app.media.composition.ffmpeg import FfmpegCompositionService
from app.media.tts.base import TTSProvider
from app.media.tts.huggingface import HuggingFaceTtsProvider
from app.media.tts.kokoro import KokoroTtsProvider
from app.media.tts.nvidia import NvidiaTtsProvider
from app.media.tts.router import TTSRouter
from app.media.tts.supervised import SupervisedTtsProvider
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

    The media-band analogue of `ResearchBundle` (ADR 0044): the model client the
    TTS supervisor uses, the httpx clients the wired NVIDIA/HuggingFace TTS
    fallbacks use (owned by the composition root, not the adapters — ADR 0050),
    and the stock visual provider when configured all own clients the app closes
    on shutdown. The local Kokoro backend and ``FfmpegCompositionService`` own no
    client, so they are not closables.
    """

    deps: MediaDeps
    closables: tuple[AsyncClosable, ...]


#: TTS fallback policy (ADR 0050): cheapest/most-local first. The router walks
#: this order on failure, and its head is the `default_backend` the supervisor
#: clamps an out-of-set model choice to. Kokoro is always present (local, no
#: key); nvidia/huggingface join only when their key is set, so the *registered*
#: order is this list intersected with what was wired.
_TTS_FALLBACK_ORDER: tuple[str, ...] = (
    KokoroTtsProvider.name,
    NvidiaTtsProvider.name,
    HuggingFaceTtsProvider.name,
)


def _build_tts_provider(
    settings: Settings,
    *,
    model_router: ModelRouter,
    audio_sink: Callable[[bytes], str],
    closables: list[AsyncClosable],
) -> TTSProvider:
    """Assemble the supervised TTS router the media pipeline consumes (ADR 0050).

    Builds a `TTSRouter` whose backends are config-gated:

    * **Kokoro** (local ONNX, no key) is *always* registered — this is what makes
      a Kokoro-only setup render with no TTS service account. It needs only the
      two model files; their paths default to non-empty filenames so the provider
      *constructs* unconditionally (the files are read lazily at synth time, and
      the doctor checks their presence).
    * **NVIDIA** / **HuggingFace** join the router *only when their key is set* —
      so a missing fallback key is silent (the local default still delivers),
      never a `CompositionError`. Each owns an `httpx.AsyncClient`; those adapters
      do not expose ``aclose``, so the composition root *owns* the client (injects
      it, then registers the client itself — which satisfies `AsyncClosable` — in
      ``closables`` for the lifespan to drain). Kokoro owns no client.

    The router is wrapped in a `TTSSupervisorAgent` (the §4 judgment seam: the
    ``PLANNING`` model picks the backend/voice; the router guarantees delivery via
    ordered fallback) and exposed to the pipeline as a plain `TTSProvider` via
    `SupervisedTtsProvider` — so the pipeline's ``tts.synthesize`` contract is
    unchanged. Note ``tts_backend`` is **not** read here: it only selects the
    doctor's readiness row. At render time the supervisor chooses per beat among
    all wired backends, and the router's policy head (always Kokoro) is both the
    default and the fallback — so the local backend always guarantees output.
    """
    # httpx clients the adapters use; the composition root owns + closes them
    # (the adapters expose no aclose). The default timeout matches each adapter's
    # own (60s) so injecting a client does not silently change request behavior.
    providers: dict[str, TTSProvider] = {
        KokoroTtsProvider.name: KokoroTtsProvider(
            model_path=settings.kokoro_model_path,
            voices_path=settings.kokoro_voices_path,
            sink=audio_sink,
            voice=settings.tts_voice,
        )
    }
    if settings.nvidia_tts_api_key.get_secret_value():
        nvidia_client = httpx.AsyncClient(timeout=60.0)
        providers[NvidiaTtsProvider.name] = NvidiaTtsProvider(
            base_url=settings.nvidia_tts_base_url,
            api_key=settings.nvidia_tts_api_key.get_secret_value(),
            sink=audio_sink,
            model=settings.nvidia_tts_model,
            client=nvidia_client,
        )
        closables.append(nvidia_client)
    if settings.huggingface_tts_api_key.get_secret_value():
        hf_client = httpx.AsyncClient(timeout=60.0)
        providers[HuggingFaceTtsProvider.name] = HuggingFaceTtsProvider(
            model=settings.huggingface_tts_model,
            token=settings.huggingface_tts_api_key.get_secret_value(),
            sink=audio_sink,
            client=hf_client,
        )
        closables.append(hf_client)

    # Register only the backends actually wired, in cheapest-first policy order.
    fallback_order = tuple(name for name in _TTS_FALLBACK_ORDER if name in providers)
    router = TTSRouter(providers=providers, fallback_order=fallback_order)
    supervisor = TTSSupervisorAgent(model_router, router)
    return SupervisedTtsProvider(supervisor)


def build_media_deps(settings: Settings | None = None) -> MediaBundle:
    """Assemble the live `MediaDeps` bundle (+ its closables) from settings (ADR 0050).

    Wires the real media seams for an end-to-end render:

    * A **supervised TTS router** (`_build_tts_provider`): a `TTSRouter` over the
      local-default `KokoroTtsProvider` plus any configured NVIDIA/HuggingFace
      fallbacks, wrapped in a `TTSSupervisorAgent` and presented to the pipeline as
      a `TTSProvider`. Critically, a **Kokoro-only setup (no TTS service key)
      builds** — TTS no longer hard-requires a service account. A **filesystem
      audio sink** writes synthesized audio under ``media_output_dir`` and returns
      a ``file://`` URI (the scheme the ffmpeg adapter resolves).
    * `FfmpegCompositionService` writing into ``media_output_dir`` (ADR 0023).
    * `StockVisualProvider` (ADR 0024) **and** a `visual_sink` when
      ``stock_api_key`` is set: the provider mints remote ``https`` B-roll uris,
      and the sink fetches each to a local file under ``media_output_dir`` and
      returns its ``file://`` uri — the bridge ffmpeg needs (without it the
      remote uri would raise in `resolve_local_path`). Both are ``None`` when no
      stock key is set; a live render then has no visual and fails loudly in
      ffmpeg (the honest behavior, not a silent default).

    The TTS supervisor needs a `ModelRouter` (the §4 judgment seam), so this
    builder mints its own (via `_build_router`) — an unconfigured model backend
    surfaces as a loud `CompositionError`. That router is independent of the one
    `build_research_deps` builds; the end-to-end path therefore holds two model
    clients (a small, documented cost — ADR 0050 — for keeping the media build
    self-contained and `build_research_deps` untouched). This live path is config
    + ffmpeg-binary + network gated; the hermetic tests inject a fake-backed
    `MediaDeps` directly and never reach here.
    """
    resolved = settings or get_settings()

    output_dir = Path(resolved.media_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _audio_sink(audio_bytes: bytes) -> str:
        # Persist the opaque audio blob next to the rendered video and return a
        # file:// URI the ffmpeg adapter can resolve (ADR 0019 storage seam: the
        # adapter is storage-neutral; this composition root chooses the filesystem).
        path = output_dir / f"{_unique_token('aud')}.audio"
        path.write_bytes(audio_bytes)
        return path.resolve().as_uri()

    closables: list[AsyncClosable] = []
    model_router, model_provider = _build_router(resolved)
    closables.append(cast(AsyncClosable, model_provider))
    tts = _build_tts_provider(
        resolved,
        model_router=model_router,
        audio_sink=_audio_sink,
        closables=closables,
    )
    composition = FfmpegCompositionService(output_dir=output_dir)

    visuals: VisualProvider | None = None
    visual_sink: VisualSink | None = None
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
