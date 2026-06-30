"""Tests for the default role->model policy (M2)."""

from __future__ import annotations

from app.core.config import Settings
from app.services.llm.base import ModelRole
from app.services.llm.policy import default_policy


def test_default_policy_covers_all_roles() -> None:
    policy = default_policy(Settings())
    assert set(policy.keys()) == set(ModelRole)


def test_default_policy_uses_configured_provider_and_models() -> None:
    s = Settings()
    policy = default_policy(s)
    assert policy[ModelRole.PLANNING].provider == s.default_provider
    assert policy[ModelRole.PLANNING].model == s.planning_model
    assert policy[ModelRole.FALLBACK].model == s.fallback_model


def test_default_policy_respects_env_override() -> None:
    s = Settings(planning_model="custom-model")
    policy = default_policy(s)
    assert policy[ModelRole.PLANNING].model == "custom-model"


def test_default_policy_treats_whitespace_provider_as_default() -> None:
    # A whitespace-only per-role provider override must fall back to the default
    # provider, not be passed through as a (later unroutable) provider name.
    s = Settings(
        default_provider="anthropic",
        planning_provider="   ",
        extraction_provider="\t",
        long_context_provider=" ",
        fallback_provider="\n",
    )
    policy = default_policy(s)
    assert policy[ModelRole.PLANNING].provider == "anthropic"
    assert policy[ModelRole.EXTRACTION].provider == "anthropic"
    assert policy[ModelRole.LONG_CONTEXT].provider == "anthropic"
    assert policy[ModelRole.FALLBACK].provider == "anthropic"
