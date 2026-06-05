"""Budget accounting + enforcement core for the model fabric (services layer).

This module is the *deterministic service half* of the cost-guardrail story
(CLAUDE.md §4): mechanical, provider-neutral, stdlib-only machinery that meters
estimated LLM spend and **refuses a call that would push spend past a configured
ceiling**. The *judgment* half — how to react when a run is blocked (abandon,
downgrade the role, escalate) — stays with the Research Orchestrator (ADR 0005,
which deferred "retries/budgets" to a consumer; this is that consumer).

The pieces:

- :class:`CostEstimator` — a tiny protocol that turns a request
  ``(model, system, prompt)`` into an estimated dollar cost *before* the call.
  Two stdlib implementations ship: :class:`PerCallEstimator` (flat $/call per
  model) and :class:`TokenCostEstimator` ($/1k tokens via a pluggable, default
  heuristic token counter). Estimation is **input-only and pre-call** — see the
  estimate-vs-actual caveat below.
- :class:`PriceTable` — a configurable per-model price map. A model missing from
  the table **fails loud** (:class:`UnknownModelError`), never silently $0 — a
  silent-$0 default would let an unmodeled model bypass the budget entirely.
- :class:`BudgetLimits` — optional, independent per-run and per-day ceilings.
- :class:`BudgetTracker` — accrues usage (call counts + estimated cost) scoped
  per-run and per-day (calendar-day via an injected clock) and enforces the
  ceilings, raising :class:`BudgetExceededError` *before* a call is made.

**Estimate-vs-actual caveat (the core honesty point).** The
:class:`~app.services.llm.base.ModelProvider` contract returns only the
schema-validated result, never token-usage metadata, so this guardrail cannot
know the *actual* billed cost of a call. It enforces against a *pre-call
estimate* derived from the input (and, for the token estimator, a heuristic
token count). The estimate is therefore approximate and, deliberately, counts
the *attempt*: a request is accrued at check-time (before the call), so a call
that later fails still consumes budget. Both directions are the safe ones for a
spend guardrail — it errs toward blocking sooner, never toward overshooting
silently. Tune the price table / token counter to your provider's real billing;
treat the tally as a conservative upper-bound ceiling, not an invoice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

# An injected clock returning an aware ``datetime``. Defaults to UTC ``now`` for
# production; tests pass a stub so the calendar-day rollover is asserted without
# real time passing.
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BudgetError(RuntimeError):
    """Base class for budget-layer configuration/usage errors."""


class UnknownModelError(BudgetError):
    """Raised when a cost estimate is requested for a model absent from the price table.

    Mirrors the router's fail-loud posture (``UnknownProviderError``): an
    unmodeled model must never be silently estimated as free, because a $0
    estimate would let it bypass the budget ceiling entirely.
    """


@dataclass(frozen=True)
class BudgetExceededError(BudgetError):
    """Raised *before* a call when accruing its estimate would breach a ceiling.

    Carries which ceiling tripped and the figures behind the decision so a caller
    (or the Orchestrator) can log/route on it rather than parse a string.

    Attributes:
        scope: ``"run"`` or ``"day"`` — which ceiling was breached.
        limit: The configured ceiling for that scope (dollars).
        accrued: Spend already accrued in that scope before this call.
        projected: ``accrued`` + the blocked call's estimate (dollars).
    """

    scope: str
    limit: float
    accrued: float
    projected: float

    def __str__(self) -> str:
        return (
            f"per-{self.scope} budget exceeded: projected ${self.projected:.6f} "
            f"> limit ${self.limit:.6f} (already accrued ${self.accrued:.6f})"
        )


@runtime_checkable
class TokenCounter(Protocol):
    """Pluggable text->token-count estimator (deterministic, no model call)."""

    def count(self, text: str) -> int:
        """Return an estimated token count for ``text``."""
        ...


class HeuristicTokenCounter:
    """A deterministic, dependency-free token-count heuristic (~4 chars/token).

    A coarse stand-in for a real tokenizer (``tiktoken`` et al.), chosen so the
    guardrail stays stdlib-only and fully hermetic. ``chars_per_token`` is
    tunable; the count is ``ceil(len(text) / chars_per_token)`` so any non-empty
    text counts as at least one token.
    """

    def __init__(self, *, chars_per_token: int = 4) -> None:
        if chars_per_token <= 0:
            raise BudgetError("chars_per_token must be a positive int")
        self._chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        return -(-len(text) // self._chars_per_token)  # ceil division


@dataclass(frozen=True)
class PriceTable:
    """Per-model price map for cost estimation.

    The ``prices`` mapping is keyed by model id; its *meaning* (dollars per call
    vs dollars per 1k tokens) is defined by the :class:`CostEstimator` that reads
    it. A model absent from the map raises :class:`UnknownModelError` from
    :meth:`price_for` — never a silent $0 — unless ``default_price`` is set
    *deliberately* by the caller, which then applies to any unlisted model.
    """

    prices: Mapping[str, float]
    default_price: float | None = None

    def price_for(self, model: str) -> float:
        """Return the configured price for ``model``.

        Raises :class:`UnknownModelError` if the model is unlisted and no
        ``default_price`` was set.
        """
        if model in self.prices:
            return self.prices[model]
        if self.default_price is not None:
            return self.default_price
        raise UnknownModelError(
            f"no price configured for model {model!r} and no default_price set; "
            "an unmodeled model must not silently bypass the budget"
        )


@runtime_checkable
class CostEstimator(Protocol):
    """Turns a request into an estimated dollar cost *before* the call is made."""

    def estimate(self, *, model: str, system: str, prompt: str) -> float:
        """Return the estimated dollar cost of the request."""
        ...


class PerCallEstimator:
    """A :class:`CostEstimator` charging a flat per-call price per model.

    Reads :class:`PriceTable` as dollars-per-call: each request costs
    ``price_for(model)`` regardless of prompt size. The simplest honest estimate
    when only call-count caps matter.
    """

    def __init__(self, price_table: PriceTable) -> None:
        self._prices = price_table

    def estimate(self, *, model: str, system: str, prompt: str) -> float:
        return self._prices.price_for(model)


class TokenCostEstimator:
    """A :class:`CostEstimator` charging by estimated input tokens.

    Reads :class:`PriceTable` as dollars-per-1k-tokens and multiplies by the
    token count of ``system + prompt`` (via the injected :class:`TokenCounter`,
    default :class:`HeuristicTokenCounter`). This is an **input-only** estimate:
    the provider contract returns no completion-token usage, so output tokens are
    not counted — part of the estimate-vs-actual caveat (module docstring).
    """

    def __init__(
        self,
        price_table: PriceTable,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._prices = price_table
        self._counter: TokenCounter = token_counter or HeuristicTokenCounter()

    def estimate(self, *, model: str, system: str, prompt: str) -> float:
        price_per_1k = self._prices.price_for(model)
        tokens = self._counter.count(system) + self._counter.count(prompt)
        return price_per_1k * tokens / 1000.0


@dataclass(frozen=True)
class BudgetLimits:
    """Optional, independent per-run and per-day spend ceilings (dollars).

    Each ceiling is ``None``-able; ``None`` means "no cap for that scope". The
    tracker checks both and blocks if *either* would be exceeded. A ceiling must
    be non-negative when set.
    """

    per_run: float | None = None
    per_day: float | None = None

    def __post_init__(self) -> None:
        for label, value in (("per_run", self.per_run), ("per_day", self.per_day)):
            if value is not None and value < 0:
                raise BudgetError(f"{label} ceiling must be non-negative")


@dataclass(frozen=True)
class UsageSnapshot:
    """An immutable read-out of accrued usage at a point in time."""

    run_calls: int
    run_cost: float
    day: date
    day_calls: int
    day_cost: float


@dataclass
class BudgetTracker:
    """Accrues estimated LLM usage and enforces per-run / per-day ceilings.

    The tracker is a value-holding service, not an agent: it accrues call counts
    and estimated cost scoped to the current *run* (its whole lifetime) and the
    current *calendar day* (via the injected clock), and refuses a charge that
    would breach a configured ceiling.

    Enforcement is **pre-call reservation**: a consumer calls :meth:`charge` with
    the request's estimate; the tracker projects ``accrued + estimate`` against
    each ceiling and raises :class:`BudgetExceededError` if *either* would be
    exceeded, otherwise it accrues immediately and returns. Accruing at
    check-time (before the call) closes the concurrency window where two in-flight
    calls both pass a check before either records, and conservatively counts the
    attempt even if the call later fails — the safe direction for a spend
    guardrail.

    Day scoping is calendar-day by the injected clock's UTC date: each
    :meth:`charge` compares the clock's date to the stored day and resets the
    daily accumulators on rollover (the per-run accumulators never reset).

    Boundary: "would exceed" is strict ``>`` — a projection landing *exactly* on
    the ceiling is allowed; only a projection *over* it is blocked.
    """

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    clock: Clock = _utc_now

    run_calls: int = field(default=0, init=False)
    run_cost: float = field(default=0.0, init=False)
    _day: date | None = field(default=None, init=False)
    _day_calls: int = field(default=0, init=False)
    _day_cost: float = field(default=0.0, init=False)

    def _roll_day(self) -> date:
        """Return today's date, resetting the daily accumulators on rollover."""
        today = self.clock().date()
        if self._day != today:
            self._day = today
            self._day_calls = 0
            self._day_cost = 0.0
        return today

    def charge(self, cost: float) -> None:
        """Reserve ``cost`` against the budget, or raise before it is spent.

        Projects ``accrued + cost`` for each configured scope; raises
        :class:`BudgetExceededError` (run checked before day) if either ceiling
        would be exceeded. On success, accrues the cost and a call to both scopes.

        Raises :class:`BudgetError` if ``cost`` is negative.
        """
        if cost < 0:
            raise BudgetError("cost must be non-negative")
        self._roll_day()

        run_projected = self.run_cost + cost
        if self.limits.per_run is not None and run_projected > self.limits.per_run:
            raise BudgetExceededError(
                scope="run",
                limit=self.limits.per_run,
                accrued=self.run_cost,
                projected=run_projected,
            )
        day_projected = self._day_cost + cost
        if self.limits.per_day is not None and day_projected > self.limits.per_day:
            raise BudgetExceededError(
                scope="day",
                limit=self.limits.per_day,
                accrued=self._day_cost,
                projected=day_projected,
            )

        self.run_calls += 1
        self.run_cost = run_projected
        self._day_calls += 1
        self._day_cost = day_projected

    def snapshot(self) -> UsageSnapshot:
        """Return an immutable read-out of current usage (rolling the day first)."""
        today = self._roll_day()
        return UsageSnapshot(
            run_calls=self.run_calls,
            run_cost=self.run_cost,
            day=today,
            day_calls=self._day_calls,
            day_cost=self._day_cost,
        )
