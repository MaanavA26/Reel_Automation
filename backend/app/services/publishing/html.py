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

Escaping alone does **not** neutralize a dangerous url *scheme* — an
``href="javascript:..."`` survives ``html.escape`` and still executes on click.
So the citation ``source_url`` is additionally checked against a scheme
allowlist (``http``/``https``/``mailto``); a non-allowlisted scheme renders the
citation label as escaped text with no link at all (ADR 0043).

This fulfills ADR 0017's deferred HTML renderer (the consumer has now arrived).
See ADR 0017 and ADR 0043 (fetch/render hardening).
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from app.schemas.research_state import Caveat, Citation, Report, ReportSection

# Only these url schemes may become a clickable ``href``. ``html.escape`` does
# NOT neutralize a ``javascript:``/``data:`` scheme, so a non-allowlisted scheme
# is rendered as escaped text with no link (ADR 0043). ``mailto`` is kept for
# author/contact citations.
_LINKABLE_SCHEMES = frozenset({"http", "https", "mailto"})


def _is_linkable(url: str) -> bool:
    """True if ``url``'s scheme is on the href allowlist (XSS guard).

    Leading whitespace / control chars are stripped first so a
    ``"\\tjavascript:..."`` style payload cannot smuggle a disallowed scheme
    past the check. A scheme-relative or relative url (empty scheme) is treated
    as non-linkable and rendered as plain escaped text.
    """
    cleaned = url.strip().lstrip("\t\r\n\x00")
    return urlsplit(cleaned).scheme.lower() in _LINKABLE_SCHEMES


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
        label = escape(citation.title or citation.source_url)
        source_type = escape(citation.source_type.value)
        if _is_linkable(citation.source_url):
            href = escape(citation.source_url, quote=True)
            entry = f'<a href="{href}">{label}</a>'
        else:
            # Non-allowlisted scheme (e.g. javascript:/data:) — no link, text only.
            entry = label
        lines.append(f"    <li>{entry} — {source_type}</li>")
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
