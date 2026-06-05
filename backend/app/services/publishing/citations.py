"""Deterministic citation assembly — the report's source-grounded bibliography.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): given the
findings a report actually cites, it walks the provenance chain
``Finding -> Verdict -> Evidence -> Source`` and produces one `Citation` per
distinct source, snapshotting the source url/title for an export-readable
bibliography. The model authors no part of this, so a published report can never
cite a source that does not exist (the §11 guard, one layer past M9/M10). A
verdict's *contradicting* evidence is walked alongside its supporting evidence,
so a conflicting source is cited rather than hidden. See ADR 0017.

Dropping contract: evidence whose ``source_id`` does not resolve to a real
`Source` is *dropped with a logged warning* (never silently). A `Citation`
snapshots the source url/title for export, so it must rest on a resolved source;
the `Evidence`-only snapshot is not a sufficient substitute. The warning makes a
dropped citation an observable signal rather than a silent gap.
"""

from __future__ import annotations

import logging

from app.schemas.research_state import (
    Citation,
    Evidence,
    Finding,
    Source,
    SourceType,
    Verdict,
)

logger = logging.getLogger(__name__)


def assemble_citations(
    findings: list[Finding],
    verdicts: list[Verdict],
    evidence: list[Evidence],
    sources: list[Source],
) -> list[Citation]:
    """Build the bibliography for ``findings`` (the report's *cited* findings).

    Walks each finding's supporting verdicts → their supporting *and*
    contradicting evidence → the backing sources, grouping into one `Citation`
    per distinct source (in first-seen order — deterministic given the input
    order). Dangling ids (a verdict/evidence not in the provided sets) are
    skipped silently; evidence whose source is *missing* is dropped with a logged
    warning (it cannot be snapshotted into an export-readable citation). The
    bibliography therefore only ever references real, resolvable sources.
    """
    verdict_by_id = {v.id: v for v in verdicts}
    evidence_by_id = {e.id: e for e in evidence}
    source_by_id = {s.id: s for s in sources}

    # Accumulate per source, preserving first-seen order for determinism.
    by_source: dict[str, _CitationAccumulator] = {}
    for finding in findings:
        for vid in finding.supporting_verdict_ids:
            verdict = verdict_by_id.get(vid)
            if verdict is None:
                continue
            # Cite supporting and contradicting evidence alike, so a conflicting
            # source surfaces in the bibliography rather than being hidden.
            for eid in (*verdict.supporting_evidence_ids, *verdict.contradicting_evidence_ids):
                ev = evidence_by_id.get(eid)
                if ev is None:
                    continue
                acc = by_source.get(ev.source_id)
                if acc is None:
                    src = source_by_id.get(ev.source_id)
                    if src is None:
                        # A citation must snapshot a resolved source; an unresolved
                        # source cannot be cited. Warn so the drop is observable.
                        logger.warning(
                            "Dropping citation for evidence %s: source %s not in sources",
                            ev.id,
                            ev.source_id,
                        )
                        continue
                    acc = _CitationAccumulator(
                        source_id=ev.source_id,
                        source_url=src.url,
                        source_type=src.type,
                        title=src.title,
                    )
                    by_source[ev.source_id] = acc
                acc.add(evidence_id=ev.id, verdict_id=verdict.id)

    return [acc.build() for acc in by_source.values()]


class _CitationAccumulator:
    """Mutable per-source accumulator; emits a `Citation` once fully walked."""

    def __init__(
        self,
        *,
        source_id: str,
        source_url: str,
        source_type: SourceType,
        title: str | None,
    ) -> None:
        self.source_id = source_id
        self.source_url = source_url
        self.source_type = source_type
        self.title = title
        self._evidence_ids: list[str] = []
        self._verdict_ids: list[str] = []

    def add(self, *, evidence_id: str, verdict_id: str) -> None:
        if evidence_id not in self._evidence_ids:
            self._evidence_ids.append(evidence_id)
        if verdict_id not in self._verdict_ids:
            self._verdict_ids.append(verdict_id)

    def build(self) -> Citation:
        # Every accumulator has a resolved source by construction (evidence with
        # an unresolved source is dropped before an accumulator is created).
        return Citation(
            source_id=self.source_id,
            source_url=self.source_url,
            source_type=self.source_type,
            title=self.title,
            evidence_ids=self._evidence_ids,
            verdict_ids=self._verdict_ids,
        )
