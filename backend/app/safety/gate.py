"""The deterministic pre-publish content-safety gate (a tool, CLAUDE.md §4).

`PrePublishGate` evaluates a published `Report` + its `CreatorPacket` + the
produced `PublishCandidate` (script/metadata) against a publish-readiness policy
and returns a typed `SafetyVerdict`. It is the structural "don't auto-post
misinformation" guard that ties the §11 *code-derived* caveats/warnings to a
single publish decision:

- **BLOCK** when the content rests on a contradicted/disputed finding
  (a `DISPUTED_FINDING` caveat on the report or a disputed `CreatorWarning` on
  the packet), or carries an `UNRESOLVED_CRITIQUE` caveat (the exhausted-revision
  banner) **without** a disclaimer on the candidate.
- **REVIEW** (hold for a human) when a configured banned topic/keyword matches the
  candidate's text, or the report falls below the minimum source-grounding floor.
- **ALLOW** otherwise.

Pure and fully unit-testable: no LLM, no I/O, no clock — the verdict is a function
of the inputs and the constructor-supplied `GatePolicy`. The policy is configured
via constructor args (not `Settings`), keeping the gate config-root-agnostic and
trivially testable with bespoke thresholds. It **trusts** the §11 caveats/warnings
rather than re-deriving them (those are code-derived and non-omittable upstream;
see `services/publishing/caveats.py`). See ADR 0041.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.safety.verdict import (
    SafetyDecision,
    SafetyReason,
    SafetyReasonKind,
    SafetyVerdict,
    Severity,
)
from app.schemas.research_state import CaveatKind, CreatorPacket, Report

# Caveat/warning kinds that mark content as resting on contradicted sources.
# Defined as a set so the disputed check is a single membership test shared by
# the report-caveat and packet-warning scans (they can never drift apart).
_DISPUTED_KINDS: frozenset[CaveatKind] = frozenset({CaveatKind.DISPUTED_FINDING})


class PublishCandidate(BaseModel):
    """The produced short-form content about to be published — the gate's third input.

    This is the gate's own DTO (it cannot touch `schemas/`): the script text and
    publish metadata the Media/publishing layer produced, plus an **explicit**
    ``disclaimer`` field. The disclaimer is a structured flag, deliberately *not*
    a fuzzy scan of the script text — the "unresolved critique without a
    disclaimer" rule must key off an unambiguous signal, so the producer states
    whether a disclaimer is attached rather than the gate guessing.

    The banned-keyword scan reads ``title`` + ``description`` + ``script_text``
    (the operator-visible publish surface); ``packet_id`` is retained for re-join
    to the source `CreatorPacket`.
    """

    model_config = ConfigDict(extra="forbid")

    packet_id: str
    title: str
    description: str = ""
    script_text: str = ""
    disclaimer: str | None = None

    @property
    def has_disclaimer(self) -> bool:
        """True iff a non-blank disclaimer is attached to this candidate."""
        return self.disclaimer is not None and self.disclaimer.strip() != ""

    def text_surface(self) -> str:
        """The concatenated publish surface the banned-keyword scan ranges over."""
        return "\n".join((self.title, self.description, self.script_text))


@dataclass(frozen=True)
class GatePolicy:
    """Explainable, constructor-configured policy for the pre-publish gate.

    Every knob is explicit and defaulted so a `PrePublishGate()` is usable out of
    the box, yet each can be overridden per call site without touching `Settings`
    (CLAUDE.md §10 — keep the gate config-root-agnostic and testable).

    Attributes:
        banned_keywords: topics/keywords whose presence routes to human REVIEW.
            Matched case-insensitively. By default a *whole-word* match (so
            ``"scunthorpe"`` does not trip a ban on ``"thorpe"``); set
            ``banned_keyword_whole_word=False`` for substring matching.
        banned_keyword_whole_word: whole-word (default) vs substring matching.
        min_distinct_sources: the source-grounding floor — the minimum number of
            **distinct** sources cited by the report (mirrors the
            CORROBORATED ">=2 distinct sources" semantics). Below it → REVIEW.
        block_on_unresolved_critique_without_disclaimer: when True (default), an
            `UNRESOLVED_CRITIQUE` caveat with no disclaimer on the candidate is a
            BLOCK; with a disclaimer it is suppressed to REVIEW.
        banned_keyword_severity: severity for a banned-keyword hit. REVIEW by
            default (keyword matching has false positives); set to
            ``Severity.BLOCK`` to hard-block banned topics.
    """

    banned_keywords: frozenset[str] = frozenset()
    banned_keyword_whole_word: bool = True
    min_distinct_sources: int = 2
    block_on_unresolved_critique_without_disclaimer: bool = True
    banned_keyword_severity: Severity = Severity.REVIEW

    def normalized_banned_keywords(self) -> frozenset[str]:
        """The banned keywords lowercased + stripped of blanks (matching is case-insensitive)."""
        return frozenset(k.strip().lower() for k in self.banned_keywords if k.strip())


class PrePublishGate:
    """Deterministic publish-readiness gate over a `Report` + `CreatorPacket` + candidate.

    Construct with an optional `GatePolicy`; call `evaluate` to obtain a typed
    `SafetyVerdict`. Stateless and pure — the same inputs always produce an equal
    verdict.
    """

    def __init__(self, policy: GatePolicy | None = None) -> None:
        self._policy = policy if policy is not None else GatePolicy()

    @property
    def policy(self) -> GatePolicy:
        return self._policy

    def evaluate(
        self,
        report: Report,
        packet: CreatorPacket,
        candidate: PublishCandidate,
    ) -> SafetyVerdict:
        """Evaluate the publish policy and return the explained verdict.

        Runs every rule (does not short-circuit), collects one `SafetyReason` per
        triggered rule, and derives the decision as the maximum severity over the
        reasons. The reason order is stable: disputed-content first (BLOCK signals),
        then the unresolved-critique check, then banned keywords, then grounding.
        """
        reasons: list[SafetyReason] = []
        reasons.extend(self._disputed_content_reasons(report, packet))
        reasons.extend(self._unresolved_critique_reasons(report, candidate))
        reasons.extend(self._banned_keyword_reasons(candidate))
        reasons.extend(self._grounding_reasons(report))

        decision = self._decide(reasons)
        return SafetyVerdict(decision=decision, reasons=reasons)

    # -- individual rules ---------------------------------------------------

    def _disputed_content_reasons(
        self, report: Report, packet: CreatorPacket
    ) -> list[SafetyReason]:
        """BLOCK on any disputed (contradicted-source) finding in the report/packet.

        Reads the §11 code-derived signals on both surfaces: a `DISPUTED_FINDING`
        caveat on the report and a disputed `CreatorWarning` on the packet. Either
        means a published claim rests on contradictory sources — the canonical
        misinformation-risk case — so it is a hard BLOCK.
        """
        reasons: list[SafetyReason] = []

        disputed_report_caveats = [c for c in report.caveats if c.kind in _DISPUTED_KINDS]
        if disputed_report_caveats:
            reasons.append(
                SafetyReason(
                    kind=SafetyReasonKind.DISPUTED_FINDING,
                    severity=Severity.BLOCK,
                    detail=(
                        f"Report carries {len(disputed_report_caveats)} disputed-finding "
                        "caveat(s); content rests on contradictory sources."
                    ),
                )
            )

        disputed_packet_warnings = [w for w in packet.warnings if w.kind in _DISPUTED_KINDS]
        if disputed_packet_warnings:
            reasons.append(
                SafetyReason(
                    kind=SafetyReasonKind.DISPUTED_FINDING,
                    severity=Severity.BLOCK,
                    detail=(
                        f"Creator packet carries {len(disputed_packet_warnings)} disputed "
                        "unsafe-claim warning(s); a hook/angle may rest on contradictory sources."
                    ),
                )
            )

        return reasons

    def _unresolved_critique_reasons(
        self, report: Report, candidate: PublishCandidate
    ) -> list[SafetyReason]:
        """BLOCK on an unresolved editorial critique unless a disclaimer is attached.

        An `UNRESOLVED_CRITIQUE` caveat means the revision loop exhausted while the
        Editorial Critic was still unsatisfied (the conclusions are provisional). If
        the candidate ships a disclaimer the risk is acknowledged → REVIEW, not a
        hard BLOCK; with no disclaimer → BLOCK.
        """
        if not self._policy.block_on_unresolved_critique_without_disclaimer:
            return []
        if not any(c.kind is CaveatKind.UNRESOLVED_CRITIQUE for c in report.caveats):
            return []

        if candidate.has_disclaimer:
            return [
                SafetyReason(
                    kind=SafetyReasonKind.UNRESOLVED_CRITIQUE,
                    severity=Severity.REVIEW,
                    detail=(
                        "Report's editorial review was unresolved; a disclaimer is attached, "
                        "so hold for human confirmation rather than auto-publishing."
                    ),
                )
            ]
        return [
            SafetyReason(
                kind=SafetyReasonKind.UNRESOLVED_CRITIQUE,
                severity=Severity.BLOCK,
                detail=(
                    "Report's editorial review was unresolved (revision budget exhausted) "
                    "and no disclaimer is attached; do not auto-publish provisional conclusions."
                ),
            )
        ]

    def _banned_keyword_reasons(self, candidate: PublishCandidate) -> list[SafetyReason]:
        """Flag any configured banned topic/keyword found in the candidate's text.

        One reason per matched keyword (so the operator sees every hit), at the
        policy's configured severity (REVIEW by default). Matching is
        case-insensitive over the candidate's title+description+script surface.
        """
        keywords = self._policy.normalized_banned_keywords()
        if not keywords:
            return []

        surface = candidate.text_surface().lower()
        reasons: list[SafetyReason] = []
        # Sorted for a deterministic, stable reason order across runs.
        for keyword in sorted(keywords):
            if self._keyword_matches(keyword, surface):
                reasons.append(
                    SafetyReason(
                        kind=SafetyReasonKind.BANNED_KEYWORD,
                        severity=self._policy.banned_keyword_severity,
                        detail=f"Candidate text matched banned keyword/topic: {keyword!r}.",
                    )
                )
        return reasons

    def _keyword_matches(self, keyword: str, surface_lower: str) -> bool:
        """Whether ``keyword`` (already lowercased) occurs in the lowercased surface."""
        if self._policy.banned_keyword_whole_word:
            return re.search(rf"\b{re.escape(keyword)}\b", surface_lower) is not None
        return keyword in surface_lower

    def _grounding_reasons(self, report: Report) -> list[SafetyReason]:
        """Require a minimum number of distinct cited sources (REVIEW below the floor).

        Counts **distinct** ``source_id``s across the report's citations (matching
        the CORROBORATED ">=2 distinct sources" semantics) rather than raw citation
        count, so duplicate citations to one source cannot satisfy the floor.
        """
        threshold = self._policy.min_distinct_sources
        if threshold <= 0:
            return []

        distinct_sources = {c.source_id for c in report.citations}
        if len(distinct_sources) >= threshold:
            return []

        return [
            SafetyReason(
                kind=SafetyReasonKind.INSUFFICIENT_GROUNDING,
                severity=Severity.REVIEW,
                detail=(
                    f"Report cites {len(distinct_sources)} distinct source(s); "
                    f"policy requires at least {threshold}."
                ),
            )
        ]

    # -- decision -----------------------------------------------------------

    @staticmethod
    def _decide(reasons: list[SafetyReason]) -> SafetyDecision:
        """The verdict's decision = the maximum severity over the reasons (none → ALLOW)."""
        if not reasons:
            return SafetyDecision.ALLOW
        return max(r.severity for r in reasons).decision
