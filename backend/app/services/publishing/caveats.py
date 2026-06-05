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
        if finding.disputed:
            caveats.append(
                Caveat(
                    kind=CaveatKind.DISPUTED_FINDING,
                    detail=f"Finding rests on contradictory sources: {finding.statement}",
                    finding_ids=[finding.id],
                )
            )
        elif finding.weakest_support is SupportLevel.SINGLE_SOURCE:
            caveats.append(
                Caveat(
                    kind=CaveatKind.WEAK_SUPPORT,
                    detail=f"Finding is supported by a single source: {finding.statement}",
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
