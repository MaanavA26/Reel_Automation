"""Deterministic creator-packet warnings — the packet's non-omittable warnings.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): it projects the
already-code-derived finding grounding into a creator packet's `CreatorWarning`
list, so a punchy hook can never quietly rest on a disputed or single-source
finding without the unsafe/unverified-claim warning surfacing. The model is given
no field to author or omit these — this is the §11 keystone of the creator packet,
one layer past M11's caveats.

Critically, the warnings range over the **full** synthesis findings, *independent
of which findings the creative elements happen to reference* — exactly as M11's
caveats range over the full findings set rather than the cited subset. Otherwise
the model could bury a contradiction simply by not citing the disputed finding in
any hook/angle/narrative. The cross-reference back to a creative element is by
**shared ``finding_ids``** (a warning's findings intersected with an element's
code-resolved findings). Reuses M11's `finding_caveat_kind` predicate so the two
surfaces never drift on what counts as unsafe. See ADR 0018.
"""

from __future__ import annotations

from app.schemas.research_state import CreatorWarning, Finding
from app.services.publishing.caveats import _finding_caveat_detail, finding_caveat_kind


def derive_creator_warnings(findings: list[Finding]) -> list[CreatorWarning]:
    """Derive a creator packet's warnings from the full findings set.

    One `CreatorWarning` per disputed or single-source finding, in findings
    order — the finding-level subset of M11's caveats, sharing the exact same
    `finding_caveat_kind` predicate and code-templated detail. Deterministic:
    identical inputs yield an identical list. A packet over only clean findings
    yields no warnings; a packet over all-disputed findings is heavily warned but
    still valid (the thin-is-the-product inversion at the creator surface).
    """
    warnings: list[CreatorWarning] = []
    for finding in findings:
        kind = finding_caveat_kind(finding)
        if kind is not None:
            warnings.append(
                CreatorWarning(
                    kind=kind,
                    detail=_finding_caveat_detail(kind, finding),
                    finding_ids=[finding.id],
                )
            )
    return warnings
