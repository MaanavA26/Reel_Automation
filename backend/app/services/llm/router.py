"""Role-based model router — the policy-driven selection fabric.

The router maps a logical `ModelRole` to a concrete provider + model via a
configured policy and returns a `BoundModel` ready to call. Selection is pure
dictionary lookup with explicit, typed failure modes — a service, not an agent
(CLAUDE.md §4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.llm.base import ModelProvider, ModelRole, StructuredT


class ModelRoutingError(RuntimeError):
    """Base class for routing-time configuration errors."""


class UnknownRoleError(ModelRoutingError):
    """Raised when a role has no entry in the active policy."""


class UnknownProviderError(ModelRoutingError):
    """Raised when a policy entry names a provider that is not registered."""


@dataclass(frozen=True)
class ModelChoice:
    """A concrete ``(provider, model)`` selection for a role."""

    provider: str
    model: str


# A complete routing policy: every role maps to a concrete model choice.
RolePolicy = Mapping[ModelRole, ModelChoice]


class BoundModel:
    """A provider bound to a concrete model id, ready to call.

    Returned by `ModelRouter.for_role` so callers invoke the model without
    repeating the model id — the role has already resolved it.
    """

    def __init__(self, provider: ModelProvider, model: str) -> None:
        self._provider = provider
        self._model = model

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._model

    async def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        return await self._provider.complete_structured(
            model=self._model,
            system=system,
            prompt=prompt,
            schema=schema,
        )


class ModelRouter:
    """Resolves a `ModelRole` to a `BoundModel` via the active policy.

    Holds a registry of named providers and a role->`ModelChoice` policy. A
    policy may reference a provider that is not (yet) registered — `for_role`
    fails loudly with `UnknownProviderError` in that case rather than selecting
    a wrong model silently.
    """

    def __init__(
        self,
        providers: Mapping[str, ModelProvider],
        policy: RolePolicy,
    ) -> None:
        self._providers: dict[str, ModelProvider] = dict(providers)
        self._policy: dict[ModelRole, ModelChoice] = dict(policy)

    def for_role(self, role: ModelRole) -> BoundModel:
        """Return the model bound to ``role``.

        Raises `UnknownRoleError` if the policy has no entry for the role, or
        `UnknownProviderError` if the entry names an unregistered provider.
        """
        try:
            choice = self._policy[role]
        except KeyError as exc:
            raise UnknownRoleError(f"no policy entry for role {role!r}") from exc
        try:
            provider = self._providers[choice.provider]
        except KeyError as exc:
            raise UnknownProviderError(
                f"policy maps role {role!r} to unregistered provider {choice.provider!r}"
            ) from exc
        return BoundModel(provider=provider, model=choice.model)
