"""Tests for the named provider-preset registry (ADR 0028).

Fully hermetic — no network. They assert the registry resolves each known name
to the expected ``base_url``, that ``build_provider`` wires an
``OpenAICompatibleProvider`` (reading the per-provider key from ``Settings`` and
failing loud when a required key is missing), and that an unknown name raises the
typed error. Live calls are not needed (and not made).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.providers import (
    PROVIDER_REGISTRY,
    MissingProviderKeyError,
    UnknownProviderPresetError,
    build_provider,
)

_EXPECTED_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "ollama": "http://localhost:11434/v1",
}

# A Settings with every key-requiring preset's key populated, so build_provider
# does not fail the missing-key guard. (Ollama is keyless.)
_KEYED_SETTINGS = Settings(
    groq_api_key=SecretStr("gsk_test"),
    nvidia_api_key=SecretStr("nv_test"),
    huggingface_api_key=SecretStr("hf_test"),
)


def test_registry_resolves_expected_base_urls() -> None:
    assert set(PROVIDER_REGISTRY) == set(_EXPECTED_BASE_URLS)
    for name, expected in _EXPECTED_BASE_URLS.items():
        assert PROVIDER_REGISTRY[name].base_url == expected


@pytest.mark.parametrize("name", sorted(_EXPECTED_BASE_URLS))
def test_build_provider_wires_openai_compatible(name: str) -> None:
    provider = build_provider(name, _KEYED_SETTINGS)
    assert isinstance(provider, OpenAICompatibleProvider)


def test_build_provider_sends_per_provider_key_on_the_wire() -> None:
    # Verify the configured key reaches the Authorization header (the codebase
    # pattern for key wiring; see test_openai_compatible.py), not just the field.
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = build_provider("groq", Settings(groq_api_key=SecretStr("gsk_secret")))
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class _Empty(BaseModel):
        pass

    asyncio.run(provider.complete_structured(model="m", system="s", prompt="p", schema=_Empty))
    assert seen["auth"] == "Bearer gsk_secret"


def test_keyless_local_preset_builds_without_a_key() -> None:
    # Ollama needs no key; build still satisfies the adapter's bearer-token call.
    provider = build_provider("ollama", Settings())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert not PROVIDER_REGISTRY["ollama"].requires_key


def test_missing_required_key_raises_typed_error() -> None:
    with pytest.raises(MissingProviderKeyError) as exc:
        build_provider("groq", Settings())  # no groq_api_key configured
    assert "groq" in str(exc.value)


def test_unknown_name_raises_typed_error() -> None:
    with pytest.raises(UnknownProviderPresetError) as exc:
        build_provider("does-not-exist", _KEYED_SETTINGS)
    # The message lists the known presets to aid the operator.
    assert "groq" in str(exc.value)
