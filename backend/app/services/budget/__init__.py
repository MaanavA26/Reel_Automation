"""Budget guardrails — estimated-spend metering + enforcement (services layer).

A deterministic *service* (CLAUDE.md §4), not an agent: it meters estimated LLM
spend and **refuses a call that would breach a configured per-run / per-day
ceiling**, so unattended runs cannot rack up runaway API cost. This is the
consumer ADR 0005 deferred "retries/budgets" to.

Two composable pieces, mirroring the model fabric's decorator pattern
(`CachingModelProvider`, `ResilientModelProvider`):

- :class:`BudgetTracker` + a pluggable :class:`CostEstimator` (per-call or
  per-token) over a configurable :class:`PriceTable` — the accounting +
  enforcement core.
- :class:`BudgetedModelProvider` — a `ModelProvider` decorator that estimates,
  reserves, and blocks *before* the wrapped call, so a blocked call incurs no
  real spend.

Enforcement is against a *pre-call estimate* (the provider contract returns no
token-usage metadata); see :mod:`app.services.budget.tracker` for the full
estimate-vs-actual caveat.
"""

from __future__ import annotations

from app.services.budget.provider import BudgetedModelProvider
from app.services.budget.tracker import (
    BudgetError,
    BudgetExceededError,
    BudgetLimits,
    BudgetTracker,
    CostEstimator,
    HeuristicTokenCounter,
    PerCallEstimator,
    PriceTable,
    TokenCostEstimator,
    TokenCounter,
    UnknownModelError,
    UsageSnapshot,
)

__all__ = [
    "BudgetError",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetTracker",
    "BudgetedModelProvider",
    "CostEstimator",
    "HeuristicTokenCounter",
    "PerCallEstimator",
    "PriceTable",
    "TokenCostEstimator",
    "TokenCounter",
    "UnknownModelError",
    "UsageSnapshot",
]
