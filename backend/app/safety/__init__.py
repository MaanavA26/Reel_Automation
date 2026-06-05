"""Pre-publish content-safety guardrail — the deterministic publish gate.

Per CLAUDE.md §4 this package holds a *tool/service* (no judgment, no LLM): a
pure, deterministic policy check that ties the §11 code-derived caveats/warnings
to a single publish decision. The `PrePublishGate` evaluates a `Report` +
`CreatorPacket` (+ the produced `PublishCandidate` script/metadata) against an
explainable, constructor-configurable policy and returns a typed `SafetyVerdict`
(ALLOW / BLOCK / REVIEW + the reasons that drove it).

This is the structural "don't auto-post misinformation" guard: it BLOCKs content
that rests on contradicted/disputed findings or carries an unresolved editorial
critique without a disclaimer, flags a configurable banned-topic list for human
REVIEW, and requires a minimum source-grounding floor. See ADR 0041.
"""

from app.safety.gate import GatePolicy, PrePublishGate, PublishCandidate
from app.safety.verdict import (
    SafetyDecision,
    SafetyReason,
    SafetyReasonKind,
    SafetyVerdict,
)

__all__ = [
    "GatePolicy",
    "PrePublishGate",
    "PublishCandidate",
    "SafetyDecision",
    "SafetyReason",
    "SafetyReasonKind",
    "SafetyVerdict",
]
