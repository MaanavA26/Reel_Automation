"""Deterministic caveat derivation — the report's non-omittable limitations.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): it projects the
already-code-derived reasoning facts into a report's `Caveat` list, so the
report's limitations can never be silently dropped however glowing the model's
prose. The model is given no field to author or omit caveats — this is the §11
keystone of the publishing band.

Critically, the disputed/weak caveats range over the **full** synthesis findings,
not only the findings the report happened to cite: otherwise the model could bury
a contradiction simply by not citing the disputed finding in any section. See
ADR 0017.
"""

from __future__ import annotations

from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Critique,
    CritiqueDecision,
    Finding,
    SupportLevel,
)


def finding_caveat_kind(finding: Finding) -> CaveatKind | None:
    """Classify a single finding's grounding into a finding-level caveat kind.

    Returns ``DISPUTED_FINDING`` for a contradicted finding, ``WEAK_SUPPORT`` for
    a (non-disputed) single-source finding, or ``None`` for a cleanly-corroborated
    one. The **single** predicate over a finding's code-derived grounding flags,
    shared by the report's `derive_caveats` (M11) and the creator packet's
    `derive_creator_warnings` (M12) so the two surfaces can never drift on what
    counts as unsafe/unverified. See ADR 0018.
    """
    if finding.disputed:
        return CaveatKind.DISPUTED_FINDING
    if finding.weakest_support is SupportLevel.SINGLE_SOURCE:
        return CaveatKind.WEAK_SUPPORT
    return None


def _finding_caveat_detail(kind: CaveatKind, finding: Finding) -> str:
    """Code-templated detail string for a finding-level caveat/warning kind."""
    if kind is CaveatKind.DISPUTED_FINDING:
        return f"Finding rests on contradictory sources: {finding.statement}"
    return f"Finding is supported by a single source: {finding.statement}"


def derive_caveats(findings: list[Finding], latest_critique: Critique | None) -> list[Caveat]:
    """Derive the report's caveats from the full findings + the latest critique.

    Caveats, in order: each disputed finding (contradictory sources), each
    single-source finding (thin support), then — from ``latest_critique`` — the
    uncovered sub-questions, each carried-forward quality issue, and, when the
    last critique still reads ``REVISE`` (which by the router invariant means the
    revision loop *exhausted unsatisfied*; ADR 0012), an unresolved-critique
    banner. Deterministic: identical inputs yield an identical list.
    """
    caveats: list[Caveat] = []

    for finding in findings:
        kind = finding_caveat_kind(finding)
        if kind is not None:
            caveats.append(
                Caveat(
                    kind=kind,
                    detail=_finding_caveat_detail(kind, finding),
                    finding_ids=[finding.id],
                )
            )

    if latest_critique is not None:
        if latest_critique.uncovered_sub_question_ids:
            n = len(latest_critique.uncovered_sub_question_ids)
            caveats.append(
                Caveat(
                    kind=CaveatKind.UNCOVERED_SUB_QUESTION,
                    detail=f"{n} sub-question(s) were not addressed by any finding.",
                    sub_question_ids=list(latest_critique.uncovered_sub_question_ids),
                    critique_id=latest_critique.id,
                )
            )
        for issue in latest_critique.issues:
            caveats.append(
                Caveat(
                    kind=CaveatKind.QUALITY_ISSUE,
                    detail=f"Editorial note ({issue.kind.value}): {issue.detail}",
                    finding_ids=list(issue.finding_ids),
                    sub_question_ids=list(issue.sub_question_ids),
                    critique_id=latest_critique.id,
                )
            )
        if latest_critique.decision is CritiqueDecision.REVISE:
            caveats.append(
                Caveat(
                    kind=CaveatKind.UNRESOLVED_CRITIQUE,
                    detail=(
                        "The editorial review was not satisfied and the revision budget was "
                        "exhausted; treat these conclusions as provisional."
                    ),
                    critique_id=latest_critique.id,
                )
            )

    return caveats
