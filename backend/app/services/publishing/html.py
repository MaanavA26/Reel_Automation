"""Deterministic HTML projection of a `Report` — the band-D rendered body.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM): the HTML twin of
`markdown.render_markdown`. It projects an already-built, typed `Report` into a
readable HTML fragment, rendering only what the report carries and fabricating
nothing (the provenance/§11 boundary carried to the output surface).

The same two integrity points hold as for the Markdown renderer — **citations**
and **caveats** always render when present (ADR 0017 / CLAUDE.md §11) — plus one
HTML-specific obligation: **all model/user text is escaped**. Text content goes
through ``html.escape`` and the citation ``source_url`` goes through
``html.escape(..., quote=True)`` before it is interpolated into the ``href``
attribute, so a quote or angle bracket in a title, narrative, caveat detail, or
url cannot break out of its element or attribute context.

This fulfills ADR 0017's deferred HTML renderer (the consumer has now arrived).
See ADR 0017.
"""

from __future__ import annotations

from html import escape

from app.schemas.research_state import Caveat, Citation, Report, ReportSection


def render_html(report: Report) -> str:
    """Render a typed `Report` as a deterministic, escaped HTML fragment.

    Structure mirrors `render_markdown`: an ``<h1>`` title, an abstract
    paragraph, one ``<section>`` per report section, a References ``<ol>`` built
    from the code-derived citations, and a Limitations ``<section>`` built from
    the non-omittable caveats. Empty sections / References / Limitations are
    omitted, but a non-empty ``citations`` or ``caveats`` list *always* renders.
    All interpolated text is HTML-escaped (urls with ``quote=True`` for the
    ``href`` attribute context). Pure and deterministic: given order, no clock.
    """
    blocks: list[str] = [
        f"<h1>{escape(report.title)}</h1>",
        f"<p>{escape(report.abstract)}</p>",
    ]

    for section in report.sections:
        blocks.append(_render_section(section))

    if report.citations:
        blocks.append(_render_references(report.citations))

    if report.caveats:
        blocks.append(_render_caveats(report.caveats))

    return "\n".join(blocks) + "\n"


def _render_section(section: ReportSection) -> str:
    return (
        "<section>\n"
        f"  <h2>{escape(section.heading)}</h2>\n"
        f"  <p>{escape(section.narrative)}</p>\n"
        "</section>"
    )


def _render_references(citations: list[Citation]) -> str:
    lines = ["<section>", "  <h2>References</h2>", "  <ol>"]
    for citation in citations:
        href = escape(citation.source_url, quote=True)
        label = escape(citation.title or citation.source_url)
        source_type = escape(citation.source_type.value)
        lines.append(f'    <li><a href="{href}">{label}</a> — {source_type}</li>')
    lines.extend(["  </ol>", "</section>"])
    return "\n".join(lines)


def _render_caveats(caveats: list[Caveat]) -> str:
    lines = ["<section>", "  <h2>Limitations &amp; Caveats</h2>", "  <ul>"]
    for caveat in caveats:
        kind = escape(caveat.kind.value)
        detail = escape(caveat.detail)
        lines.append(f"    <li><strong>{kind}</strong> — {detail}</li>")
    lines.extend(["  </ul>", "</section>"])
    return "\n".join(lines)
