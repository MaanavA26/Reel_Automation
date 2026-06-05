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

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.services.composition import (
    CompositionError,
    MediaDeps,
    _build_model_provider,
    _build_router,
    _build_search_provider,
    build_media_deps,
    build_research_deps,
)
from app.services.llm.base import ModelRole
from app.services.llm.gemini import GeminiProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider
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
    router, _provider = _build_router(
        _settings(default_provider="groq", groq_api_key=SecretStr("gsk-test"))
    )
    bound = router.for_role(ModelRole.PLANNING)  # must not raise
    assert bound.provider_name == "openai-compatible"  # the adapter's own name


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


# --- Media deps assembly -----------------------------------------------------


def test_build_media_deps_requires_tts_base_url() -> None:
    with pytest.raises(CompositionError, match="TTS_BASE_URL"):
        build_media_deps(_settings())


def test_build_media_deps_requires_tts_key() -> None:
    with pytest.raises(CompositionError, match="TTS_API_KEY"):
        build_media_deps(_settings(tts_base_url="https://tts.example.com"))


def test_build_media_deps_builds_seams(tmp_path: object) -> None:
    bundle = build_media_deps(
        _settings(
            tts_base_url="https://tts.example.com",
            tts_api_key=SecretStr("tts-key"),
            media_output_dir=str(tmp_path),
        )
    )
    deps = bundle.deps
    assert isinstance(deps, MediaDeps)
    assert deps.tts.name == "http"
    assert deps.composition.name == "ffmpeg"
    # No stock key configured -> no visual provider wired (live render would then
    # fail loud in ffmpeg, the honest behavior).
    assert deps.visuals is None
    # Only the TTS provider owns a client to close (no stock key) (ADR 0044).
    assert len(bundle.closables) == 1


def test_build_media_deps_wires_stock_visuals_and_sink_when_key_set(tmp_path: object) -> None:
    bundle = build_media_deps(
        _settings(
            tts_base_url="https://tts.example.com",
            tts_api_key=SecretStr("tts-key"),
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
    # Both TTS and stock visuals own clients to close (ADR 0044).
    assert len(bundle.closables) == 2


def test_no_stock_key_wires_neither_visuals_nor_sink(tmp_path: object) -> None:
    deps = build_media_deps(
        _settings(
            tts_base_url="https://tts.example.com",
            tts_api_key=SecretStr("tts-key"),
            media_output_dir=str(tmp_path),
        )
    ).deps
    assert deps.visuals is None
    assert deps.visual_sink is None


def test_filesystem_visual_sink_passes_local_uris_through() -> None:
    from app.services.composition import _make_filesystem_visual_sink

    sink = _make_filesystem_visual_sink(Path("/tmp"))
    # A bare path / file:// uri is already local — returned unchanged, no fetch.
    assert sink("/tmp/clip.mp4") == "/tmp/clip.mp4"
    assert sink("file:///tmp/clip.mp4") == "file:///tmp/clip.mp4"
