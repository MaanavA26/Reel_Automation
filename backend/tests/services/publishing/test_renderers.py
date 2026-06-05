"""Tests for the deterministic `Report` renderers (M11, ADR 0017 deferred renderer).

Pins the output-surface guarantees the renderers exist to carry:

- **Citations always render** when present — the report's provenance reaches the
  document (no "no provenance on research outputs", CLAUDE.md §11).
- **Caveats always render** when present — the §11 non-omittability of the
  publishing band carried to the rendered surface.
- **HTML escaping** of model/user text in *both* text content and the citation
  ``href`` attribute context.
- A thin report (no citations / caveats) emits no empty scaffolding.
"""

from __future__ import annotations

from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Citation,
    Report,
    ReportSection,
    SourceType,
)
from app.services.publishing.html import render_html
from app.services.publishing.markdown import render_markdown


def _report(
    *,
    title: str = "A Title",
    abstract: str = "An abstract.",
    sections: list[ReportSection] | None = None,
    citations: list[Citation] | None = None,
    caveats: list[Caveat] | None = None,
) -> Report:
    return Report(
        title=title,
        abstract=abstract,
        sections=sections if sections is not None else [],
        citations=citations if citations is not None else [],
        caveats=caveats if caveats is not None else [],
        published_via="report:fake",
    )


def _citation(*, url: str = "https://example.com/a", title: str | None = "Example A") -> Citation:
    return Citation(
        source_id="src_1",
        source_url=url,
        source_type=SourceType.WEB,
        title=title,
    )


def _caveat(*, detail: str = "a contradiction was found") -> Caveat:
    return Caveat(kind=CaveatKind.DISPUTED_FINDING, detail=detail)


# --- title / abstract / sections render -------------------------------------


def test_markdown_renders_title_abstract_and_sections() -> None:
    section = ReportSection(heading="Background", narrative="Some narrative.")
    out = render_markdown(_report(sections=[section]))
    assert "# A Title" in out
    assert "An abstract." in out
    assert "## Background" in out
    assert "Some narrative." in out


def test_html_renders_title_abstract_and_sections() -> None:
    section = ReportSection(heading="Background", narrative="Some narrative.")
    out = render_html(_report(sections=[section]))
    assert "<h1>A Title</h1>" in out
    assert "<p>An abstract.</p>" in out
    assert "<h2>Background</h2>" in out
    assert "<p>Some narrative.</p>" in out


# --- citations always render when present (provenance reaches the surface) ---


def test_markdown_always_renders_citations() -> None:
    cits = [
        _citation(url="https://example.com/a", title="Example A"),
        _citation(url="https://example.com/b", title=None),
    ]
    out = render_markdown(_report(citations=cits))
    assert "## References" in out
    for c in cits:
        assert c.source_url in out
    assert "Example A" in out  # titled citation uses its title as label


def test_html_always_renders_citations() -> None:
    cits = [_citation(url="https://example.com/a"), _citation(url="https://example.com/b")]
    out = render_html(_report(citations=cits))
    assert "<h2>References</h2>" in out
    for c in cits:
        assert f'href="{c.source_url}"' in out


# --- caveats always render when present (§11 non-omittability) ---------------


def test_markdown_always_renders_every_caveat_detail() -> None:
    caveats = [
        _caveat(detail="first limitation"),
        _caveat(detail="second limitation"),
    ]
    out = render_markdown(_report(caveats=caveats))
    assert "## Limitations & Caveats" in out
    for c in caveats:
        assert c.detail in out


def test_html_always_renders_every_caveat_detail() -> None:
    caveats = [_caveat(detail="first limitation"), _caveat(detail="second limitation")]
    out = render_html(_report(caveats=caveats))
    assert "Limitations" in out
    for c in caveats:
        assert c.detail in out


# --- thin report: no empty scaffolding ---------------------------------------


def test_thin_report_omits_empty_sections() -> None:
    md = render_markdown(_report())
    html = render_html(_report())
    for out in (md, html):
        assert "References" not in out
        assert "Limitations" not in out


# --- HTML escaping in both text content and the href attribute ---------------


def test_html_escapes_text_and_href() -> None:
    # A quote and an angle bracket in *both* a text field and the url.
    report = _report(
        title='Title <script> "x"',
        abstract='Abstract & "quoted" <b>',
        sections=[ReportSection(heading="<h>", narrative='narrative "q" <i>')],
        citations=[_citation(url='https://example.com/?q="<x>', title='Link <a> "t"')],
        caveats=[_caveat(detail='caveat <c> "d"')],
    )
    out = render_html(report)

    # No raw angle brackets from user text leak as element-opening syntax.
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;h&gt;" in out
    assert "&lt;c&gt;" in out
    # The raw url with its unescaped quote/brackets must not appear in href.
    assert 'href="https://example.com/?q="<x>"' not in out
    assert "?q=&quot;&lt;x&gt;" in out  # url escaped with quote=True for the attribute
    # Text-context double quotes are escaped to &quot; (html.escape default).
    assert '"quoted"' not in out
    assert "&quot;quoted&quot;" in out


# --- determinism --------------------------------------------------------------


def test_renderers_are_deterministic() -> None:
    report = _report(
        sections=[ReportSection(heading="H", narrative="N")],
        citations=[_citation()],
        caveats=[_caveat()],
    )
    assert render_markdown(report) == render_markdown(report)
    assert render_html(report) == render_html(report)
