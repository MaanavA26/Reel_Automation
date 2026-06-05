"""Deterministic Markdown projection of a `Report` — the band-D rendered body.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): it projects an
already-built, typed `Report` into a readable Markdown document. It renders only
what the report carries — title, abstract, sections, the code-derived
bibliography, and the **non-omittable** caveats — and fabricates nothing (the
provenance/§11 boundary carried to the output surface).

Two integrity points the renderer must honour, both inherited from ADR 0017:

- **Citations always render** when present: the report's source-grounded
  bibliography is its provenance, and dropping it would re-open the very
  "no provenance on research outputs" anti-pattern (CLAUDE.md §11) at the export.
- **Caveats always render** when present: the §11 keystone of the publishing
  band is that a report's limitations are non-omittable; the renderer carries
  that guarantee to the document by always emitting the Limitations section
  whenever ``report.caveats`` is non-empty.

This fulfills ADR 0017's deferred Markdown renderer (the consumer has now
arrived). See ADR 0017.
"""

from __future__ import annotations

from app.schemas.research_state import Caveat, Citation, Report, ReportSection


def render_markdown(report: Report) -> str:
    """Render a typed `Report` as a deterministic Markdown document.

    The document is title → abstract → sections (heading + narrative) →
    References (the code-derived bibliography) → Limitations (the non-omittable
    caveats). Empty sections / References / Limitations headings are omitted — a
    thin report does not emit empty scaffolding — but whenever ``citations`` or
    ``caveats`` are non-empty their sections are *always* rendered. Pure and
    deterministic: lists render in their given order and no clock is read.
    """
    blocks: list[str] = [f"# {report.title}", report.abstract]

    for section in report.sections:
        blocks.append(_render_section(section))

    if report.citations:
        blocks.append(_render_references(report.citations))

    if report.caveats:
        blocks.append(_render_caveats(report.caveats))

    return "\n\n".join(blocks) + "\n"


def _render_section(section: ReportSection) -> str:
    return f"## {section.heading}\n\n{section.narrative}"


def _render_references(citations: list[Citation]) -> str:
    lines = ["## References", ""]
    for index, citation in enumerate(citations, start=1):
        label = citation.title or citation.source_url
        lines.append(f"{index}. [{label}]({citation.source_url}) — {citation.source_type.value}")
    return "\n".join(lines)


def _render_caveats(caveats: list[Caveat]) -> str:
    lines = ["## Limitations & Caveats", ""]
    for caveat in caveats:
        lines.append(f"- **{caveat.kind.value}** — {caveat.detail}")
    return "\n".join(lines)
