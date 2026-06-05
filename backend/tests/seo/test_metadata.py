"""Tests for the deterministic SEO `MetadataBuilder`.

Fully hermetic and equality-oriented: the builder is a pure function over a
`CreatorPacket` + `Report`, so every assertion is a deterministic check with no
LLM and no I/O. The tests pin the load-bearing invariants: title <= 100 chars
(exactly at the boundary), the §11 headline guard (a disputed hook/fact never
becomes the title), description ordering (hook → key points → sources →
hashtags), source dedup, stopword-filtered tags, the hashtag cap, and the
packet/report mismatch error.
"""

from __future__ import annotations

import pytest

from app.schemas.research_state import (
    Citation,
    ContentAngle,
    CreatorPacket,
    HookIdea,
    KeyFact,
    NarrativeOption,
    Report,
    ReportSection,
    SourceType,
    SupportLevel,
)
from app.seo.metadata import (
    MAX_HASHTAGS,
    MAX_TAGS,
    MAX_TITLE_LEN,
    MetadataBuilder,
    MetadataError,
    VideoMetadata,
)


def _report(
    *,
    title: str = "Solar Power Breakthroughs",
    citations: list[Citation] | None = None,
    sections: list[ReportSection] | None = None,
) -> Report:
    return Report(
        title=title,
        abstract="An overview of recent solar technology advances.",
        sections=sections
        or [ReportSection(heading="Efficiency Gains", narrative="prose", finding_ids=["fnd_x"])],
        citations=citations or [],
        published_via="report:fake",
    )


def _packet(
    report: Report,
    *,
    hooks: list[HookIdea] | None = None,
    key_facts: list[KeyFact] | None = None,
) -> CreatorPacket:
    return CreatorPacket(
        report_id=report.id,
        hooks=hooks or [],
        angles=[],
        narratives=[],
        key_facts=key_facts or [],
        warnings=[],
        published_via="packet:fake",
    )


def _fact(
    statement: str,
    *,
    finding_id: str = "fnd_1",
    disputed: bool = False,
    support: SupportLevel = SupportLevel.CORROBORATED,
) -> KeyFact:
    return KeyFact(
        statement=statement,
        finding_id=finding_id,
        disputed=disputed,
        weakest_support=support,
    )


# --- title --------------------------------------------------------------------


def test_title_prefers_first_hook() -> None:
    report = _report()
    packet = _packet(
        report, hooks=[HookIdea(text="This solar cell broke a world record"), HookIdea(text="b")]
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.title == "This solar cell broke a world record"


def test_title_truncates_at_100_char_boundary() -> None:
    long_hook = "Word " * 40  # 200 chars, many word boundaries
    report = _report()
    packet = _packet(report, hooks=[HookIdea(text=long_hook)])
    md = MetadataBuilder().build(packet=packet, report=report)
    assert len(md.title) <= MAX_TITLE_LEN
    assert md.title.endswith("…")
    # word boundary: no split token before the ellipsis
    assert "  " not in md.title


def test_title_hard_cuts_single_overlong_word() -> None:
    report = _report()
    packet = _packet(report, hooks=[HookIdea(text="x" * 250)])
    md = MetadataBuilder().build(packet=packet, report=report)
    assert len(md.title) == MAX_TITLE_LEN
    assert md.title.endswith("…")


def test_title_skips_disputed_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first hook references a disputed finding; §11 must skip it for the title.
    report = _report()
    packet = _packet(
        report,
        hooks=[
            HookIdea(text="Disputed claim hook", finding_ids=["fnd_d"]),
            HookIdea(text="Safe hook", finding_ids=["fnd_ok"]),
        ],
        key_facts=[_fact("disputed fact", finding_id="fnd_d", disputed=True)],
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.title == "Safe hook"


def test_title_falls_back_to_safe_key_fact() -> None:
    report = _report()
    packet = _packet(
        report,
        hooks=[],
        key_facts=[
            _fact("disputed", finding_id="fnd_d", disputed=True),
            _fact("Solar efficiency hit 47 percent", finding_id="fnd_ok"),
        ],
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.title == "Solar efficiency hit 47 percent"


def test_title_falls_back_to_report_title() -> None:
    report = _report(title="Fallback Title")
    packet = _packet(report)
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.title == "Fallback Title"


# --- description --------------------------------------------------------------


def test_description_ordering_hook_then_points_then_sources_then_hashtags() -> None:
    report = _report(
        citations=[
            Citation(
                source_id="src_1",
                source_url="https://example.com/a",
                source_type=SourceType.WEB,
            )
        ]
    )
    packet = _packet(
        report,
        hooks=[HookIdea(text="Lead hook")],
        key_facts=[_fact("Efficiency rose")],
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    idx_hook = md.description.index("Lead hook")
    idx_points = md.description.index("Key points:")
    idx_sources = md.description.index("Sources:")
    idx_url = md.description.index("https://example.com/a")
    idx_hash = md.description.index("#")
    assert idx_hook < idx_points < idx_sources < idx_url < idx_hash


def test_description_dedups_source_urls() -> None:
    report = _report(
        citations=[
            Citation(source_id="s1", source_url="https://dup.com", source_type=SourceType.WEB),
            Citation(source_id="s2", source_url="https://dup.com", source_type=SourceType.PAPER),
            Citation(source_id="s3", source_url="https://other.com", source_type=SourceType.WEB),
        ]
    )
    packet = _packet(report, hooks=[HookIdea(text="Hook")])
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.description.count("https://dup.com") == 1
    assert "https://other.com" in md.description


def test_description_marks_disputed_and_weak_key_facts() -> None:
    report = _report()
    packet = _packet(
        report,
        hooks=[HookIdea(text="Hook")],
        key_facts=[
            _fact("Contested point", disputed=True),
            _fact("Thin point", support=SupportLevel.SINGLE_SOURCE),
            _fact("Solid point"),
        ],
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    assert "Contested point (note: sources disagree)" in md.description
    assert "Thin point (note: single source)" in md.description
    assert "- Solid point\n" in md.description or md.description.endswith("- Solid point")


def test_description_uses_abstract_when_no_hooks() -> None:
    report = _report()
    packet = _packet(report)
    md = MetadataBuilder().build(packet=packet, report=report)
    assert report.abstract in md.description


# --- tags / hashtags ----------------------------------------------------------


def test_tags_filter_stopwords_and_short_tokens() -> None:
    report = _report(title="The Rise of AI in Modern Solar Tech")
    packet = _packet(report)
    md = MetadataBuilder().build(packet=packet, report=report)
    assert "the" not in md.tags  # stopword
    assert "of" not in md.tags  # stopword + too short
    assert "in" not in md.tags
    assert "rise" in md.tags
    assert "solar" in md.tags


def test_tags_deduped_and_capped() -> None:
    headings = [ReportSection(heading=f"solar topic number {i}", narrative="p") for i in range(40)]
    report = _report(
        title="solar solar solar energy power grid future tech wave", sections=headings
    )
    packet = _packet(report)
    md = MetadataBuilder().build(packet=packet, report=report)
    assert len(md.tags) <= MAX_TAGS
    assert len(md.tags) == len(set(md.tags))  # deduped


def test_hashtags_are_slice_of_tags_within_cap() -> None:
    report = _report(title="alpha beta gamma delta epsilon zeta eta theta")
    packet = _packet(report)
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.hashtags == md.tags[:MAX_HASHTAGS]
    assert len(md.hashtags) <= MAX_HASHTAGS


# --- errors / contract --------------------------------------------------------


def test_packet_report_mismatch_raises() -> None:
    report = _report()
    other = _report(title="Different")
    packet = _packet(other)  # report_id points at `other`, not `report`
    with pytest.raises(MetadataError):
        MetadataBuilder().build(packet=packet, report=report)


def test_determinism_same_inputs_same_output() -> None:
    report = _report(
        citations=[Citation(source_id="s", source_url="https://x.com", source_type=SourceType.WEB)]
    )
    packet = _packet(report, hooks=[HookIdea(text="Hook")], key_facts=[_fact("Fact one")])
    a = MetadataBuilder().build(packet=packet, report=report)
    b = MetadataBuilder().build(packet=packet, report=report)
    assert a == b


def test_metadata_is_value_dto_no_id_field() -> None:
    md = VideoMetadata(title="t", description="d")
    assert md.produced_via == "seo:deterministic"
    assert not hasattr(md, "id")


def test_unused_creative_elements_do_not_break_build() -> None:
    # Angles / narratives are not consumed by the builder; ensure a full packet
    # still builds cleanly.
    report = _report()
    packet = CreatorPacket(
        report_id=report.id,
        hooks=[HookIdea(text="Hook")],
        angles=[ContentAngle(angle="A", rationale="r")],
        narratives=[NarrativeOption(title="N", script_outline="o")],
        key_facts=[_fact("F")],
        warnings=[],
        published_via="packet:fake",
    )
    md = MetadataBuilder().build(packet=packet, report=report)
    assert md.title == "Hook"
