"""Tests for the composition root's live provider selection (ADR 0032).

Hermetic: provider/adapter *construction* is offline (the network call only
happens at `complete_structured`/`search` time, which these tests never reach).
They prove the config-gated wiring exists and is correct — the right adapter is
built for each config, the router is re-keyed so the default policy resolves, and
an unconfigured backend fails loud with `CompositionError` rather than leaking a
fake into a running service.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.services.composition import (
    CompositionError,
    MediaDeps,
    _build_model_provider,
    _build_router,
    _build_search_provider,
    _is_transient_llm_error,
    build_media_deps,
    build_research_deps,
)
from app.services.llm.base import ModelRole
from app.services.llm.gemini import GeminiProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.resilience import ResilientModelProvider
from app.services.search.brave_search import BraveSearchProvider
from app.services.search.live import TavilySearchProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "default_provider": "openai-compatible",
        "base_url": "https://api.example.com/v1",
        "api_key": SecretStr("sk-test"),
        "search_provider": "tavily",
        "search_api_key": SecretStr("tvly-test"),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _wired_tts_backends(tts: object) -> set[str]:
    """The backend names registered in a supervised TTS provider's router.

    Reaches through the composition root's TTS wiring — supervised wrapper →
    supervisor → router — to assert which backends were actually wired for a
    given config (the only externally observable signal of the key-gated router).
    """
    supervisor = tts._supervisor  # type: ignore[attr-defined]
    return set(supervisor._tts_router.available())


# --- Model provider selection ------------------------------------------------


def test_openai_compatible_provider_built() -> None:
    provider = _build_model_provider(_settings())
    assert isinstance(provider, OpenAICompatibleProvider)


def test_gemini_provider_built() -> None:
    provider = _build_model_provider(
        _settings(default_provider="gemini", gemini_api_key=SecretStr("g-key"))
    )
    assert isinstance(provider, GeminiProvider)


def test_registry_preset_provider_built() -> None:
    # A named preset (groq) builds the one OpenAI-compatible adapter via the registry.
    provider = _build_model_provider(
        _settings(default_provider="groq", groq_api_key=SecretStr("gsk-test"))
    )
    assert isinstance(provider, OpenAICompatibleProvider)


def test_openai_compatible_missing_key_raises() -> None:
    with pytest.raises(CompositionError, match="REEL_AUTOMATION_API_KEY"):
        _build_model_provider(_settings(api_key=SecretStr("")))


def test_gemini_missing_key_raises() -> None:
    with pytest.raises(CompositionError, match="GEMINI_API_KEY"):
        _build_model_provider(_settings(default_provider="gemini"))


def test_registry_preset_missing_key_raises() -> None:
    with pytest.raises(CompositionError, match="API key"):
        _build_model_provider(_settings(default_provider="groq"))


def test_unknown_provider_raises() -> None:
    with pytest.raises(CompositionError, match="no model provider adapter"):
        _build_model_provider(_settings(default_provider="not-a-provider"))


# --- The re-keying invariant (advisor's sharp edge) --------------------------


def test_router_registers_provider_under_config_name_so_policy_resolves() -> None:
    # default_provider='groq' -> adapter.name is 'openai-compatible', but the
    # policy keys roles by 'groq'. Registering under the config name is what makes
    # role resolution succeed (a registry-name policy against an adapter-name key
    # would raise UnknownProviderError).
    router, _providers = _build_router(
        _settings(default_provider="groq", groq_api_key=SecretStr("gsk-test"))
    )
    bound = router.for_role(ModelRole.PLANNING)  # must not raise
    assert bound.provider_name == "openai-compatible"  # the adapter's own name


# --- Multi-provider, capability-tiered fabric (#113) -------------------------


def _tiered_settings(**overrides: object) -> Settings:
    # Bulk (extraction) on a local ollama 3B; judgment roles on cloud nvidia 70B.
    base: dict[str, object] = {
        "default_provider": "ollama",
        "extraction_provider": "ollama",
        "planning_provider": "nvidia",
        "long_context_provider": "nvidia",
        "fallback_provider": "nvidia",
        "nvidia_api_key": SecretStr("nv-test"),  # ollama needs no key
        "extraction_model": "qwen2.5:3b",
        "planning_model": "meta/llama-3.3-70b-instruct",
        "long_context_model": "meta/llama-3.3-70b-instruct",
        "fallback_model": "meta/llama-3.3-70b-instruct",
    }
    base.update(overrides)
    return _settings(**base)


def test_per_role_providers_build_distinct_registered_once() -> None:
    router, providers = _build_router(_tiered_settings())
    # Both distinct providers built + registered under their config names; each
    # built exactly once even though three roles share 'nvidia' (lifecycle: close
    # each once).
    assert set(router._providers) == {"ollama", "nvidia"}
    assert len(providers) == 2


def test_roles_resolve_to_their_configured_provider_and_model() -> None:
    router, _providers = _build_router(_tiered_settings())
    extract = router.for_role(ModelRole.EXTRACTION)
    plan = router.for_role(ModelRole.PLANNING)
    long_ctx = router.for_role(ModelRole.LONG_CONTEXT)
    assert extract.model == "qwen2.5:3b"
    assert plan.model == "meta/llama-3.3-70b-instruct"
    assert long_ctx.model == "meta/llama-3.3-70b-instruct"
    # Adapter name is shared, but each role is bound to a *distinct* provider obj.
    assert router._providers["ollama"] is not router._providers["nvidia"]


def test_schema_format_enabled_for_ollama_not_cloud() -> None:
    # Small local models need schema-constrained decoding; capable cloud ones don't.
    router, _providers = _build_router(_tiered_settings())
    assert router._providers["ollama"]._use_schema_format is True  # type: ignore[attr-defined]
    assert router._providers["nvidia"]._use_schema_format is False  # type: ignore[attr-defined]


# --- LLM resilience wiring (retry gate) ---------------------------------------


def test_retry_disabled_by_default_registers_bare_adapter() -> None:
    settings = _settings()
    router, providers = _build_router(settings)
    provider = providers[0]  # single-provider default settings
    # Reach into the registry: the gate must not wrap when max_attempts is 1.
    assert router._providers[settings.default_provider] is provider
    assert isinstance(provider, OpenAICompatibleProvider)


def test_retry_enabled_registers_wrapped_adapter_and_returns_inner() -> None:
    settings = _settings(llm_retry_max_attempts=3)
    router, providers = _build_router(settings)
    provider = providers[0]
    registered = router._providers[settings.default_provider]
    assert isinstance(registered, ResilientModelProvider)
    # Lifecycle gets the *inner* adapter (it owns the httpx client; ADR 0044).
    assert isinstance(provider, OpenAICompatibleProvider)
    assert registered.name == provider.name  # the decorator is routing-invisible


def test_transient_llm_error_narrowing() -> None:
    # 429 + 5xx + transport faults retry; auth/config 4xx propagate immediately.
    def status_error(code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://api.example.com/v1/chat")
        return httpx.HTTPStatusError(
            f"HTTP {code}", request=request, response=httpx.Response(code, request=request)
        )

    assert _is_transient_llm_error(status_error(429))
    assert _is_transient_llm_error(status_error(500))
    assert _is_transient_llm_error(status_error(503))
    assert _is_transient_llm_error(httpx.ConnectTimeout("boom"))
    assert not _is_transient_llm_error(status_error(401))
    assert not _is_transient_llm_error(status_error(404))
    assert not _is_transient_llm_error(ValueError("not http at all"))


# --- Search provider selection -----------------------------------------------


def test_tavily_search_built() -> None:
    assert isinstance(_build_search_provider(_settings()), TavilySearchProvider)


def test_brave_search_built() -> None:
    provider = _build_search_provider(
        _settings(search_provider="brave", brave_api_key=SecretStr("brv-test"))
    )
    assert isinstance(provider, BraveSearchProvider)


def test_tavily_missing_key_raises() -> None:
    with pytest.raises(CompositionError, match="SEARCH_API_KEY"):
        _build_search_provider(_settings(search_api_key=SecretStr("")))


def test_brave_missing_key_raises() -> None:
    with pytest.raises(CompositionError, match="BRAVE_API_KEY"):
        _build_search_provider(_settings(search_provider="brave"))


def test_unknown_search_provider_raises() -> None:
    with pytest.raises(CompositionError, match="no search provider adapter"):
        _build_search_provider(_settings(search_provider="not-a-search"))


# --- Full bundle assembly ----------------------------------------------------


def test_build_research_deps_assembles_full_bundle() -> None:
    bundle = build_research_deps(_settings())
    deps = bundle.deps
    # Every collaborator is present (a smoke check the bundle is complete).
    assert deps.planner and deps.discovery and deps.ingestion
    assert deps.synthesizer and deps.reporter and deps.strategist
    # The httpx-owning seams (model + search + fetch) are returned for shutdown
    # close (ADR 0044): each exposes the AsyncClosable contract.
    assert len(bundle.closables) == 3
    assert all(hasattr(c, "aclose") for c in bundle.closables)


def test_build_research_deps_unconfigured_model_raises() -> None:
    with pytest.raises(CompositionError):
        build_research_deps(_settings(api_key=SecretStr("")))


# --- Media deps assembly (TTS fabric, ADR 0050) ------------------------------


def test_build_media_deps_kokoro_only_builds_with_no_tts_service_key(tmp_path: object) -> None:
    # The keystone: a Kokoro-only setup (no NVIDIA/HF/OpenAI TTS key) builds. TTS
    # no longer hard-requires a service account — the local default needs only the
    # (lazily-read) model files.
    bundle = build_media_deps(_settings(media_output_dir=str(tmp_path)))
    deps = bundle.deps
    assert isinstance(deps, MediaDeps)
    # The pipeline consumes a single TTSProvider — the supervised-router wrapper.
    assert deps.tts.name == "supervised"
    assert deps.composition.name == "ffmpeg"
    # No stock key configured -> no visual provider wired (live render would then
    # fail loud in ffmpeg, the honest behavior).
    assert deps.visuals is None
    # Only the model client (the TTS supervisor's) is closable — Kokoro owns none.
    assert len(bundle.closables) == 1
    assert all(hasattr(c, "aclose") for c in bundle.closables)


def test_build_media_deps_requires_model_for_tts_supervisor() -> None:
    # The TTS supervisor needs a ModelRouter, so an unconfigured model fails loud.
    with pytest.raises(CompositionError):
        build_media_deps(_settings(api_key=SecretStr("")))


def test_tts_router_adds_fallbacks_only_when_their_key_set(tmp_path: object) -> None:
    # Kokoro-only -> the router registers just kokoro and owns no httpx client.
    kokoro_only = build_media_deps(_settings(media_output_dir=str(tmp_path)))
    assert _wired_tts_backends(kokoro_only.deps.tts) == {"kokoro"}

    # Set the NVIDIA + HF TTS keys -> both join the router (cheapest-first), and
    # each contributes one composition-owned httpx client to the closables.
    with_fallbacks = build_media_deps(
        _settings(
            media_output_dir=str(tmp_path),
            nvidia_tts_api_key=SecretStr("nvapi-x"),
            huggingface_tts_api_key=SecretStr("hf-x"),
        )
    )
    assert _wired_tts_backends(with_fallbacks.deps.tts) == {"kokoro", "nvidia", "huggingface"}
    # model client + nvidia client + hf client = 3 closables (no stock key).
    assert len(with_fallbacks.closables) == 3
    assert all(hasattr(c, "aclose") for c in with_fallbacks.closables)


def test_build_media_deps_wires_stock_visuals_and_sink_when_key_set(tmp_path: object) -> None:
    bundle = build_media_deps(
        _settings(
            stock_api_key=SecretStr("pexels-key"),
            media_output_dir=str(tmp_path),
        )
    )
    deps = bundle.deps
    assert deps.visuals is not None
    assert deps.visuals.name == "stock"
    # The sink is wired alongside the provider so the live render can bridge the
    # provider's remote https uris to local file:// uris ffmpeg can resolve.
    assert deps.visual_sink is not None
    # model client + stock visuals = 2 closables (Kokoro-only TTS, no fallbacks).
    assert len(bundle.closables) == 2


def test_no_stock_key_wires_neither_visuals_nor_sink(tmp_path: object) -> None:
    deps = build_media_deps(_settings(media_output_dir=str(tmp_path))).deps
    assert deps.visuals is None
    assert deps.visual_sink is None


def test_filesystem_visual_sink_passes_local_uris_through() -> None:
    from app.services.composition import _make_filesystem_visual_sink

    sink = _make_filesystem_visual_sink(Path("/tmp"))
    # A bare path / file:// uri is already local — returned unchanged, no fetch.
    assert sink("/tmp/clip.mp4") == "/tmp/clip.mp4"
    assert sink("file:///tmp/clip.mp4") == "file:///tmp/clip.mp4"
