"""Composition root: build a configured ``ModelRouter`` from ``Settings``.

The router is constructed here (and in tests); agents receive it pre-built via
dependency injection (ADR 0004). This module maps the configured
``default_provider`` to a concrete adapter — the one place provider classes are
named, keeping the agents and the rest of the fabric provider-agnostic.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.llm.base import ModelProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.policy import default_policy
from app.services.llm.router import ModelRouter


def _build_provider(settings: Settings) -> ModelProvider:
    name = settings.default_provider
    if name == OpenAICompatibleProvider.name:
        return OpenAICompatibleProvider(
            base_url=settings.base_url,
            api_key=settings.api_key.get_secret_value(),
        )
    raise ValueError(
        f"no provider adapter registered for default_provider={name!r}; set "
        f"REEL_AUTOMATION_DEFAULT_PROVIDER to a known provider "
        f"(e.g. {OpenAICompatibleProvider.name!r})"
    )


def build_router_from_settings(settings: Settings | None = None) -> ModelRouter:
    """Build a `ModelRouter` with the configured provider registered.

    The provider is registered under its own ``name``, which matches the
    ``default_provider`` the policy routes to — so role resolution succeeds.
    """
    resolved = settings or get_settings()
    provider = _build_provider(resolved)
    return ModelRouter(providers={provider.name: provider}, policy=default_policy(resolved))
