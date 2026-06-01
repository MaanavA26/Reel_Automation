"""Provider-neutral contracts for the LLM model fabric.

Defines the role taxonomy (`ModelRole`) and the `ModelProvider` protocol every
concrete provider adapter implements. Per CLAUDE.md §4, the router and its
providers are *services* (deterministic API-wrapping + selection), not agents;
agents call the router but contain no provider-specific code.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel


class ModelRole(StrEnum):
    """Logical role an agent requests, decoupled from concrete models.

    Routing is policy-driven by role (CLAUDE.md §6): an agent asks for a role,
    and the router resolves it to a provider + model id via configured policy.
    `FALLBACK` is defined here as a policy slot; the *trigger* logic that falls
    back on failure/budget exhaustion is owned by the Orchestrator (M4).
    """

    PLANNING = "planning"
    EXTRACTION = "extraction"
    LONG_CONTEXT = "long_context"
    FALLBACK = "fallback"


StructuredT = TypeVar("StructuredT", bound=BaseModel)


@runtime_checkable
class ModelProvider(Protocol):
    """A provider adapter that returns structured (schema-validated) output.

    Implementations wrap a provider SDK and coerce the model response into an
    instance of the caller's Pydantic ``schema`` (JSON mode / tool-calling is
    the adapter's concern, never the caller's). The method is async to match the
    async workflow node contract (ADR 0002), since real calls are network I/O.
    """

    name: str

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT: ...
