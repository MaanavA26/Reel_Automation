"""Model fabric — provider-neutral, policy-driven LLM routing (services layer).

Per CLAUDE.md §4 and §6, model selection is deterministic *service* work: agents
request a logical `ModelRole`; the `ModelRouter` resolves it to a concrete
provider + model via configured policy and returns a `BoundModel` ready to call.
Agents never embed provider-specific code.

The first concrete provider adapter (Anthropic) lands with its first real
consumer, the Research Planner agent (M3); this package ships the contract, the
router, the default policy, and an in-memory `FakeProvider` for hermetic tests.
"""

from __future__ import annotations

from app.services.llm.base import ModelProvider, ModelRole, StructuredT
from app.services.llm.fakes import FakeProvider, RecordedCall
from app.services.llm.policy import default_policy
from app.services.llm.router import (
    BoundModel,
    ModelChoice,
    ModelRouter,
    ModelRoutingError,
    RolePolicy,
    UnknownProviderError,
    UnknownRoleError,
)

__all__ = [
    "BoundModel",
    "FakeProvider",
    "ModelChoice",
    "ModelProvider",
    "ModelRole",
    "ModelRouter",
    "ModelRoutingError",
    "RecordedCall",
    "RolePolicy",
    "StructuredT",
    "UnknownProviderError",
    "UnknownRoleError",
    "default_policy",
]
