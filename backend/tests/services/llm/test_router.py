"""Tests for the role-based model router (M2)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import (
    BoundModel,
    ModelChoice,
    ModelRouter,
    UnknownProviderError,
    UnknownRoleError,
)


class _Out(BaseModel):
    value: str


def _router(
    policy: dict[ModelRole, ModelChoice] | None = None,
) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider([_Out(value="ok")])
    policy = policy or {ModelRole.PLANNING: ModelChoice("fake", "fake-model")}
    return ModelRouter(providers={"fake": fake}, policy=policy), fake


def test_for_role_returns_bound_model() -> None:
    router, _ = _router()
    bound = router.for_role(ModelRole.PLANNING)
    assert isinstance(bound, BoundModel)
    assert bound.provider_name == "fake"
    assert bound.model == "fake-model"


def test_unknown_role_raises() -> None:
    router, _ = _router()
    with pytest.raises(UnknownRoleError):
        router.for_role(ModelRole.EXTRACTION)


def test_unknown_provider_raises() -> None:
    # A policy may reference a provider that is not registered (e.g. the
    # default 'anthropic' policy before its adapter lands in M3); for_role
    # must fail loudly rather than mis-route.
    router = ModelRouter(
        providers={},
        policy={ModelRole.PLANNING: ModelChoice("anthropic", "claude-opus-4-8")},
    )
    with pytest.raises(UnknownProviderError):
        router.for_role(ModelRole.PLANNING)


def test_bound_model_calls_provider_with_resolved_model() -> None:
    router, fake = _router()
    bound = router.for_role(ModelRole.PLANNING)
    out = asyncio.run(bound.complete_structured(system="s", prompt="p", schema=_Out))
    assert out.value == "ok"
    assert len(fake.calls) == 1
    assert fake.calls[0].model == "fake-model"
    assert fake.calls[0].system == "s"
    assert fake.calls[0].prompt == "p"
