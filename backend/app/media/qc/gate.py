"""Map a raw `QCReport` to a publish decision — the QC gate policy (ADR 0060).

The QC *service* emits raw findings (per-check tri-state + a code-derived
summary); it does **not** decide whether to publish. That mapping —
BLOCK/REVIEW/ALLOW — is policy, and it lives here, mirroring `GatePolicy` /
`PrePublishGate` in `safety/`. Keeping it separate keeps the pure
`PrePublishGate` I/O-free (no ffprobe/subprocess ever leaks into `safety/gate.py`)
and lets the publish vocabulary stay a single source.

Vocabulary reuse (deliberate coupling): the decision enum is `SafetyDecision`
(ALLOW/REVIEW/BLOCK) and the ranking is `Severity`, both from
`app.safety.verdict`. The publish-readiness vocabulary already exists there; a
second parallel enum in the media layer would be drift, not decoupling. This is a
narrow, conscious `media → safety` import of the *decision vocabulary only* (not
the safety gate's logic). See ADR 0060 §Decision.

The default policy is deliberately conservative for the current stage:

- any **FAIL** → REVIEW by default (a real DoD miss should not auto-publish), with
  ``hard_fail_checks`` able to escalate specific checks to BLOCK;
- any **SKIPPED** check → REVIEW (the render is not fully verified — e.g.
  `CAPTION_SAFE_ZONE` is SKIPPED pending OCR, `CUT_RHYTHM` when no edit list);
- all PASS → ALLOW.

`CAPTIONS_BURNED_IN` FAILs hermetically today (no libass), so under this policy a
hermetic render is REVIEW-locked — exactly the intended autonomous-mode safeguard
until libass lands (spine C4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.media.qc.report import QCCheckKind, QCCheckStatus, QCReport
from app.safety.verdict import SafetyDecision, Severity

_STRICT = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class QCGatePolicy:
    """Explainable, constructor-configured mapping from QC findings to a decision.

    Mirrors `GatePolicy`: every knob defaulted (a bare `QCGate()` works) yet
    overridable per call site without touching `Settings` (config-root-agnostic).

    Attributes:
        fail_decision: the decision for a FAILed check not in ``hard_fail_checks``
            (default REVIEW — hold for a human rather than hard-blocking).
        skipped_decision: the decision for a SKIPPED check (default REVIEW — the
            render is not fully verified).
        hard_fail_checks: checks whose FAIL escalates to BLOCK (never auto-publish).
            Empty by default; a channel can hard-block, say, a loudness miss.
    """

    fail_decision: SafetyDecision = SafetyDecision.REVIEW
    skipped_decision: SafetyDecision = SafetyDecision.REVIEW
    hard_fail_checks: frozenset[QCCheckKind] = field(default_factory=frozenset)


class QCGateReason(BaseModel):
    """One QC finding that influenced the decision, with its severity.

    Mirrors `SafetyReason`: a ``check`` + its mapped ``severity`` + a human
    ``detail``. Only non-PASS checks produce a reason; the gate's decision is the
    max severity over the reasons (none → ALLOW).
    """

    model_config = _STRICT

    check: QCCheckKind
    severity: Severity
    detail: str


class QCGateVerdict(BaseModel):
    """The QC gate's publish decision plus the reasons that produced it.

    Mirrors `SafetyVerdict`: ``decision`` is the max severity over ``reasons``
    (no reasons → ALLOW). A pure value object — equal inputs yield equal verdicts.
    """

    model_config = _STRICT

    decision: SafetyDecision
    reasons: list[QCGateReason] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """True iff the render may be auto-published (decision is ALLOW)."""
        return self.decision is SafetyDecision.ALLOW


# SafetyDecision → Severity (the inverse of Severity.decision), so the gate can
# max over per-reason severities exactly like the safety gate.
_DECISION_TO_SEVERITY: dict[SafetyDecision, Severity] = {
    SafetyDecision.ALLOW: Severity.ALLOW,
    SafetyDecision.REVIEW: Severity.REVIEW,
    SafetyDecision.BLOCK: Severity.BLOCK,
}


class QCGate:
    """Maps a `QCReport` to a `QCGateVerdict` under a `QCGatePolicy`. Pure.

    Construct with an optional policy; call `evaluate` for the typed verdict.
    Stateless and pure — the same report always produces an equal verdict.
    """

    def __init__(self, policy: QCGatePolicy | None = None) -> None:
        self._policy = policy if policy is not None else QCGatePolicy()

    @property
    def policy(self) -> QCGatePolicy:
        return self._policy

    def evaluate(self, report: QCReport) -> QCGateVerdict:
        """Map ``report``'s non-PASS checks to a publish decision.

        One reason per non-PASS check: a FAIL maps to BLOCK if the check is in
        ``hard_fail_checks`` else to ``fail_decision``; a SKIPPED maps to
        ``skipped_decision``. The decision is the max severity over the reasons.
        """
        reasons: list[QCGateReason] = []
        for check in report.checks:
            if check.status is QCCheckStatus.PASS:
                continue
            if check.status is QCCheckStatus.FAIL:
                decision = (
                    SafetyDecision.BLOCK
                    if check.check in self._policy.hard_fail_checks
                    else self._policy.fail_decision
                )
            else:  # SKIPPED
                decision = self._policy.skipped_decision
            reasons.append(
                QCGateReason(
                    check=check.check,
                    severity=_DECISION_TO_SEVERITY[decision],
                    detail=f"[{check.status.label}] {check.detail}",
                )
            )

        decision = max((r.severity for r in reasons), default=Severity.ALLOW).decision
        return QCGateVerdict(decision=decision, reasons=reasons)
