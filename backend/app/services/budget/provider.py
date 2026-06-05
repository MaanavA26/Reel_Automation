"""Budget-enforcing decorator for the model fabric (services layer).

`BudgetedModelProvider` *wraps* any `ModelProvider` (decorator pattern —
composition, not inheritance, mirroring `CachingModelProvider` and
`ResilientModelProvider`) and turns the :class:`~app.services.budget.tracker`
core into an enforcing guardrail at the call boundary: before each
`complete_structured`, it estimates the call's cost, reserves it against the
:class:`BudgetTracker`, and lets :class:`BudgetExceededError` propagate **before
the wrapped provider is ever called** once a ceiling is hit. A call that fits
under the ceiling passes through unchanged.

Per CLAUDE.md §4 this is deterministic *service/tool* work — a mechanical spend
guard, not an agent: it makes no judgment about *how* to react to a blocked run
(abandon, downgrade, escalate); that stays with the Orchestrator (ADR 0005). It
composes around the provider-neutral `ModelProvider` contract (ADR 0003) and
contains no provider-specific code, so it is a drop-in for any provider.

**Estimate-vs-actual caveat.** Enforcement is against a *pre-call estimate*
(the provider contract returns no token-usage metadata), and the estimate is
reserved at check-time, so a blocked call is never sent and a sent call is
counted as an attempt regardless of its eventual success. See
:mod:`app.services.budget.tracker` for the full caveat.
"""

from __future__ import annotations

from app.services.budget.tracker import BudgetTracker, CostEstimator
from app.services.llm.base import ModelProvider, StructuredT


class BudgetedModelProvider:
    """A `ModelProvider` decorator that enforces a spend budget per call.

    Wraps an inner provider, a :class:`CostEstimator`, and a
    :class:`BudgetTracker`. On `complete_structured` it (1) estimates the call's
    cost from ``(model, system, prompt)``, (2) reserves it via
    ``tracker.charge`` — which raises :class:`BudgetExceededError` *before* the
    wrapped call if a configured ceiling would be exceeded — and only then (3)
    calls through. A blocked call never reaches the inner provider, so it incurs
    no real API spend.

    ``name`` wraps the inner name (``budgeted(...)``) so the guardrail is visible
    in logs/diagnostics — deliberately unlike `ResilientModelProvider` (which
    delegates the inner name); a cost guard is worth surfacing.
    """

    def __init__(
        self,
        wrapped: ModelProvider,
        *,
        estimator: CostEstimator,
        tracker: BudgetTracker,
    ) -> None:
        self._wrapped = wrapped
        self._estimator = estimator
        self._tracker = tracker
        self.name = f"budgeted({wrapped.name})"

    @property
    def tracker(self) -> BudgetTracker:
        """The underlying tracker, for read-out (``snapshot``) by a consumer."""
        return self._tracker

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        estimate = self._estimator.estimate(model=model, system=system, prompt=prompt)
        # Reserve before the call: raises BudgetExceededError here, so a blocked
        # call never reaches the wrapped provider and incurs no real spend.
        self._tracker.charge(estimate)
        return await self._wrapped.complete_structured(
            model=model, system=system, prompt=prompt, schema=schema
        )
