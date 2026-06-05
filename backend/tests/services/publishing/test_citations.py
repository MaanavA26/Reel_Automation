"""Tests for the deterministic citation-assembly tool (M11).

Pins the §11 guard: the bibliography is built by walking the real provenance
chain (Finding → Verdict → Evidence → Source); dangling ids are skipped, distinct
sources are grouped, and contradicting evidence of a disputed finding is cited too.
"""

from __future__ import annotations

from app.schemas.research_state import (
    Evidence,
    Finding,
    Source,
    SourceType,
    SupportLevel,
    Verdict,
)
from app.services.publishing.citations import assemble_citations


def _source(sid: str) -> Source:
    return Source(
        id=sid, url=f"https://{sid}.com", type=SourceType.WEB, discovered_via="search:fake"
    )


def _evidence(eid: str, source_id: str) -> Evidence:
    return Evidence(
        id=eid,
        claim="c",
        source_id=source_id,
        source_url=f"https://{source_id}.com",
        chunk_id="chk_1",
        chunk_text="t",
        confidence=0.8,
        extracted_via="extraction:fake",
    )


def _verdict(vid: str, *, supporting: list[str], contradicting: list[str] | None = None) -> Verdict:
    return Verdict(
        id=vid,
        claim="c",
        support_level=SupportLevel.CORROBORATED,
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting or [],
        confidence=0.8,
        verified_via="verification:fake",
    )


def _finding(verdict_ids: list[str]) -> Finding:
    return Finding(
        statement="f",
        supporting_verdict_ids=verdict_ids,
        disputed=False,
        weakest_support=SupportLevel.CORROBORATED,
        synthesized_via="synthesis:fake",
    )


def test_assembles_one_citation_per_source_from_chain() -> None:
    sources = [_source("src_a"), _source("src_b")]
    evidence = [_evidence("ev_1", "src_a"), _evidence("ev_2", "src_b")]
    verdicts = [_verdict("vd_1", supporting=["ev_1", "ev_2"])]
    findings = [_finding(["vd_1"])]
    citations = assemble_citations(findings, verdicts, evidence, sources)
    assert {c.source_id for c in citations} == {"src_a", "src_b"}
    cit_a = next(c for c in citations if c.source_id == "src_a")
    assert cit_a.source_url == "https://src_a.com"  # code-copied snapshot
    assert cit_a.source_type is SourceType.WEB
    assert cit_a.verdict_ids == ["vd_1"]
    assert cit_a.evidence_ids == ["ev_1"]


def test_contradicting_evidence_is_cited() -> None:
    sources = [_source("src_a"), _source("src_b")]
    evidence = [_evidence("ev_1", "src_a"), _evidence("ev_2", "src_b")]
    verdicts = [_verdict("vd_1", supporting=["ev_1"], contradicting=["ev_2"])]
    citations = assemble_citations([_finding(["vd_1"])], verdicts, evidence, sources)
    # the contradicting source is cited so the conflict is visible, not hidden:
    assert {c.source_id for c in citations} == {"src_a", "src_b"}


def test_dangling_ids_are_skipped() -> None:
    # verdict references evidence that isn't in the set; source missing too.
    verdicts = [_verdict("vd_1", supporting=["ev_missing"])]
    citations = assemble_citations([_finding(["vd_1"])], verdicts, [], [])
    assert citations == []


def test_evidence_without_resolvable_source_is_skipped() -> None:
    # evidence resolves but its source is absent → no source_type → not emitted.
    evidence = [_evidence("ev_1", "src_gone")]
    verdicts = [_verdict("vd_1", supporting=["ev_1"])]
    citations = assemble_citations([_finding(["vd_1"])], verdicts, evidence, [])
    assert citations == []
