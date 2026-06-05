"""Tests for the config-driven generative-video selector (single pick, no fallback).

Fully hermetic: the factory builds adapters from `Settings` without touching the
network (construction only). Asserts the disabled default, dispatch by name to the
right adapter type, and loud failure on unknown name / missing credentials.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.media.visuals.generative_providers.kling import KlingGenerativeProvider
from app.media.visuals.generative_providers.luma import LumaGenerativeProvider
from app.media.visuals.generative_providers.pika import PikaGenerativeProvider
from app.media.visuals.generative_providers.runway import RunwayGenerativeProvider
from app.media.visuals.generative_providers.veo import VeoGenerativeProvider
from app.media.visuals.generative_router import (
    GenerativeRoutingError,
    build_generative_visual_provider,
)


def _settings(**kwargs: object) -> Settings:
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_disabled_by_default_returns_none() -> None:
    assert build_generative_visual_provider(_settings()) is None


def test_unknown_backend_raises() -> None:
    with pytest.raises(GenerativeRoutingError, match="unknown"):
        build_generative_visual_provider(_settings(generative_video_backend="sora"))


def test_runway_dispatch() -> None:
    provider = build_generative_visual_provider(
        _settings(generative_video_backend="runway", runway_api_key=SecretStr("k"))
    )
    assert isinstance(provider, RunwayGenerativeProvider)


def test_luma_dispatch() -> None:
    provider = build_generative_visual_provider(
        _settings(generative_video_backend="luma", luma_api_key=SecretStr("k"))
    )
    assert isinstance(provider, LumaGenerativeProvider)


def test_pika_dispatch() -> None:
    provider = build_generative_visual_provider(
        _settings(generative_video_backend="pika", pika_fal_api_key=SecretStr("k"))
    )
    assert isinstance(provider, PikaGenerativeProvider)


def test_kling_dispatch() -> None:
    provider = build_generative_visual_provider(
        _settings(
            generative_video_backend="kling",
            kling_access_key=SecretStr("ak"),
            kling_secret_key=SecretStr("sk"),
        )
    )
    assert isinstance(provider, KlingGenerativeProvider)


def test_veo_dispatch() -> None:
    provider = build_generative_visual_provider(
        _settings(
            generative_video_backend="veo",
            veo_access_token=SecretStr("tok"),
            veo_project="proj",
            veo_storage_uri="gs://bucket/out",
        )
    )
    assert isinstance(provider, VeoGenerativeProvider)


def test_case_insensitive_and_trimmed() -> None:
    provider = build_generative_visual_provider(
        _settings(generative_video_backend="  Luma ", luma_api_key=SecretStr("k"))
    )
    assert isinstance(provider, LumaGenerativeProvider)


@pytest.mark.parametrize(
    "backend",
    ["runway", "luma", "pika", "kling", "veo"],
)
def test_missing_credentials_raise(backend: str) -> None:
    with pytest.raises(GenerativeRoutingError):
        build_generative_visual_provider(_settings(generative_video_backend=backend))
