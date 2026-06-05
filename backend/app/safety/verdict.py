"""Typed result of the pre-publish safety gate.

A `SafetyVerdict` is the gate's output contract: a single `SafetyDecision`
(ALLOW / BLOCK / REVIEW) plus the ordered list of `SafetyReason`s that drove it,
so the decision is fully *explainable* — every triggered policy rule surfaces a
reason carrying its own severity, and the decision is the maximum severity over
those reasons (BLOCK > REVIEW > ALLOW).

Deliberately **timestamp-free and id-free** (unlike the `*_at`/`*_via` artifacts
in `research_state`): the gate is a pure function, so a verdict is a value object
fully determined by its inputs and two identical evaluations compare equal. That
keeps the gate "pure + fully unit-testable" (CLAUDE.md §7). See ADR 0041.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class SafetyDecision(StrEnum):
    """The gate's publish-readiness decision.

    Severity order is BLOCK > REVIEW > ALLOW; a verdict's decision is the maximum
    severity over its reasons (see `Severity`). REVIEW is the deliberate middle
    rung — "do not auto-publish, route to a human" — which is the correct posture
    for signals with false positives (a banned-keyword substring match, a
    below-threshold source count) where a hard BLOCK would be too blunt.
    """

    ALLOW = "allow"  # safe to auto-publish
    REVIEW = "review"  # hold for human review (do not auto-publish)
    BLOCK = "block"  # never auto-publish (misinformation / strike risk)


class Severity(IntEnum):
    """Per-reason severity ranking, isomorphic to `SafetyDecision`.

    An `IntEnum` so the gate can take ``max(...)`` over a reason list to derive
    the verdict's decision. Carried on each `SafetyReason` (so a caller can see a
    reason's weight) while `SafetyVerdict.decision` stays the aggregate
    `SafetyDecision`.
    """

    ALLOW = 0
    REVIEW = 1
    BLOCK = 2

    @property
    def decision(self) -> SafetyDecision:
        return _SEVERITY_TO_DECISION[self]


_SEVERITY_TO_DECISION: dict[Severity, SafetyDecision] = {
    Severity.ALLOW: SafetyDecision.ALLOW,
    Severity.REVIEW: SafetyDecision.REVIEW,
    Severity.BLOCK: SafetyDecision.BLOCK,
}


class SafetyReasonKind(StrEnum):
    """Machine-readable class of a single triggered safety rule.

    Stable identifiers so a downstream surface (a publish UI, an audit log) can
    branch on *why* a verdict was reached without re-parsing the human ``detail``.
    """

    DISPUTED_FINDING = "disputed_finding"  # content rests on a contradicted finding
    UNRESOLVED_CRITIQUE = "unresolved_critique"  # exhausted editorial review, no disclaimer
    BANNED_KEYWORD = "banned_keyword"  # a configured banned topic/keyword matched
    INSUFFICIENT_GROUNDING = "insufficient_grounding"  # too few distinct sources


class SafetyReason(BaseModel):
    """One triggered policy rule, with its severity and a human explanation.

    Mirrors the `Caveat` shape (a ``kind`` + a code-templated ``detail``, no id,
    no timestamp) so the safety surface reads consistently with the publishing
    band's caveats it builds on. ``severity`` is what the gate maxes over to pick
    the verdict's decision.
    """

    model_config = _STRICT

    kind: SafetyReasonKind
    severity: Severity
    detail: str


class SafetyVerdict(BaseModel):
    """The pre-publish gate's decision plus the reasons that produced it.

    ``decision`` is derived as the maximum-severity over ``reasons`` (no reasons →
    ALLOW). The full reason list is always retained — even on a BLOCK — so the
    verdict explains *every* problem at once rather than only the first/worst,
    making one pass actionable. Pure value object: equal inputs yield equal
    verdicts (no id/timestamp).
    """

    model_config = _STRICT

    decision: SafetyDecision
    reasons: list[SafetyReason] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """True iff the content may be auto-published (decision is ALLOW)."""
        return self.decision is SafetyDecision.ALLOW
