"""LLM resilience: bounded retry + policy-driven fallback for the model fabric.

This module is the *deterministic service half* of the orchestrator's
fault-tolerance story (CLAUDE.md §4): mechanical, provider-neutral, stdlib-only
machinery that retries transient failures and hops to the configured
``FALLBACK`` model on exhaustion. The *judgment* half — when to give up, abandon,
or escalate a run — stays with the Research Orchestrator (ADR 0005). See
ADR 0027.

Two composable layers, mirroring the M2 fabric's two layers (provider vs router):

- :class:`ResilientModelProvider` — a *provider-level* decorator. It implements
  the :class:`~app.services.llm.base.ModelProvider` protocol by wrapping an inner
  provider and adding bounded retry-with-backoff on transient errors, so it is a
  drop-in the :class:`~app.services.llm.router.ModelRouter` registers without
  knowing. Retry narrows to the single API call.
- :func:`complete_with_fallback` (and the thin :class:`ResilientRouter` that
  binds a router to it) — a *router-level* helper. On the primary role's terminal
  failure it resolves the ``FALLBACK`` role via the same router policy and tries
  it **once** (policy-driven, CLAUDE.md §6 — not random multi-model chatter; one
  fallback hop, never a retry-of-retry).

Provider-neutrality is preserved by *injection*, never imports: the retryable
exception set and the (async) sleeper are constructor parameters. ``resilience``
imports no provider SDK and no ``httpx``; the transient-vs-permanent narrowing
happens at the future wiring site (the same deferral ADR 0005 named).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.services.llm.base import ModelProvider, ModelRole, StructuredT
from app.services.llm.router import BoundModel, ModelRouter, ModelRoutingError

# An async sleeper, e.g. ``asyncio.sleep``. Injected so tests pass a no-op that
# records the requested delays and the backoff schedule is asserted without
# real time passing.
AsyncSleeper = Callable[[float], Awaitable[None]]


class ResilienceError(RuntimeError):
    """Base class for resilience-layer configuration/usage errors."""


class RetryConfig:
    """Bounded retry-with-backoff policy (a value object, not an agent).

    Attributes:
        max_attempts: Total attempts including the first try. ``1`` disables
            retry. Must be >= 1.
        base_delay: Seconds to wait before the *first* retry.
        backoff_factor: Multiplier applied to the delay after each retry
            (``1.0`` = constant delay; ``2.0`` = exponential).
        max_delay: Upper bound on any single backoff delay (seconds).
        retry_on: Exception types treated as *transient* (retried). Anything
            else propagates immediately. Provider-neutral by injection — the
            wiring site narrows this to its provider's transient errors (e.g.
            ``httpx.TransportError`` / HTTP 429/5xx); see ADR 0027.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        backoff_factor: float = 2.0,
        max_delay: float = 30.0,
        retry_on: tuple[type[Exception], ...] = (Exception,),
    ) -> None:
        if max_attempts < 1:
            raise ResilienceError("max_attempts must be >= 1")
        if base_delay < 0 or max_delay < 0:
            raise ResilienceError("delays must be non-negative")
        if backoff_factor < 1:
            raise ResilienceError("backoff_factor must be >= 1")
        if not retry_on:
            raise ResilienceError("retry_on must list at least one exception type")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retry_on = retry_on

    def delay_for(self, retry_index: int) -> float:
        """Backoff delay (seconds) before retry ``retry_index`` (0-based).

        ``retry_index`` 0 is the wait before the first retry. Capped at
        ``max_delay``.
        """
        delay = self.base_delay * (self.backoff_factor**retry_index)
        return min(delay, self.max_delay)


class ResilientModelProvider:
    """A :class:`ModelProvider` decorator adding bounded retry-with-backoff.

    Wraps an inner provider and retries :meth:`complete_structured` on the
    transient errors named in :class:`RetryConfig`. A non-transient error
    propagates on the first occurrence; a transient error that persists past
    ``max_attempts`` re-raises the *last* transient error (so the caller — or the
    fallback helper — sees the real provider failure, not a synthetic wrapper).

    ``name`` delegates to the inner provider so registration in the router's
    provider map is transparent (the decorator is invisible to routing).
    """

    def __init__(
        self,
        inner: ModelProvider,
        config: RetryConfig | None = None,
        *,
        sleep: AsyncSleeper = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._config = config or RetryConfig()
        self._sleep = sleep

    @property
    def name(self) -> str:
        # Delegate so the decorated provider registers under the inner name;
        # routing and call recording stay transparent.
        return self._inner.name

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        last_error: Exception | None = None
        for attempt in range(self._config.max_attempts):
            try:
                return await self._inner.complete_structured(
                    model=model,
                    system=system,
                    prompt=prompt,
                    schema=schema,
                )
            except self._config.retry_on as exc:
                last_error = exc
                is_last = attempt == self._config.max_attempts - 1
                if is_last:
                    break
                await self._sleep(self._config.delay_for(attempt))
        # Reachable only after the transient `retry_on` path is exhausted; a
        # non-transient error would have propagated out of the `except` above.
        assert last_error is not None
        raise last_error


async def complete_with_fallback(
    router: ModelRouter,
    *,
    role: ModelRole,
    system: str,
    prompt: str,
    schema: type[StructuredT],
    fallback_role: ModelRole = ModelRole.FALLBACK,
) -> StructuredT:
    """Call ``role``; on its terminal failure, hop once to the fallback model.

    Policy-driven (CLAUDE.md §6): the fallback target is resolved from the same
    router policy via ``fallback_role`` — not a random alternate. Exactly **one**
    fallback hop is attempted; any retry lives *inside* the resolved
    :class:`BoundModel`'s provider (use a :class:`ResilientModelProvider` in the
    router's provider map to get retry), so there is no retry-of-retry here.

    Guards (each re-raises the *primary* error — what the caller cares about —
    rather than masking it with a routing error):

    - **Self-fallback:** if the fallback resolves to the same ``(provider,
      model)`` as the primary, falling back would just repeat the failure, so the
      primary error is re-raised instead.
    - **No fallback configured:** if ``fallback_role`` is absent from the policy
      (or names an unregistered provider), the router raises a
      :class:`ModelRoutingError`; we re-raise the primary provider error.
    """
    primary = router.for_role(role)
    try:
        return await primary.complete_structured(system=system, prompt=prompt, schema=schema)
    except Exception as primary_error:
        fallback = _resolve_distinct_fallback(router, role, fallback_role, primary)
        if fallback is None:
            raise primary_error
        return await fallback.complete_structured(system=system, prompt=prompt, schema=schema)


def _resolve_distinct_fallback(
    router: ModelRouter,
    role: ModelRole,
    fallback_role: ModelRole,
    primary: BoundModel,
) -> BoundModel | None:
    """Resolve the fallback model, or ``None`` if no distinct fallback applies.

    Returns ``None`` when the fallback role equals the primary role, resolves to
    the same ``(provider, model)``, or is not configured — in all of which a
    fallback hop is pointless, so the caller re-raises the primary error.
    """
    if fallback_role == role:
        return None
    try:
        fallback = router.for_role(fallback_role)
    except ModelRoutingError:
        return None
    if (fallback.provider_name, fallback.model) == (primary.provider_name, primary.model):
        return None
    return fallback


class ResilientRouter:
    """Thin wrapper binding a :class:`ModelRouter` to :func:`complete_with_fallback`.

    A convenience composition (it does not subclass or mutate the router): callers
    that want fallback-by-default get a single ``complete`` entry point while the
    underlying ``ModelRouter`` is untouched (the router stays a pure selection
    service, CLAUDE.md §4). Per-provider retry still comes from registering
    :class:`ResilientModelProvider` instances in the router's provider map.
    """

    def __init__(
        self,
        router: ModelRouter,
        *,
        fallback_role: ModelRole = ModelRole.FALLBACK,
    ) -> None:
        self._router = router
        self._fallback_role = fallback_role

    async def complete(
        self,
        *,
        role: ModelRole,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        return await complete_with_fallback(
            self._router,
            role=role,
            system=system,
            prompt=prompt,
            schema=schema,
            fallback_role=self._fallback_role,
        )
