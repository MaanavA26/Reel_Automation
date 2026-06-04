"""Tests for the pure HTML parser (M6)."""

from __future__ import annotations

import pytest

from app.services.ingestion.parser import ParseError, parse_html


def test_extracts_visible_text_skipping_script_style_head() -> None:
    html = (
        b"<html><head><title>T</title><style>.x{color:red}</style></head>"
        b"<body><p>Hello</p><script>var x=1</script><p>World</p></body></html>"
    )
    assert parse_html(html, "text/html") == "Hello World"


def test_unescapes_entities_and_collapses_whitespace() -> None:
    assert parse_html(b"<p>a &amp; b\n\n   c</p>") == "a & b c"


def test_rejects_non_html_content_type() -> None:
    with pytest.raises(ParseError):
        parse_html(b"%PDF-1.4 ...", "application/pdf")


def test_decodes_declared_charset() -> None:
    # latin-1 encoded é, declared via content-type.
    assert "café" in parse_html("<p>café</p>".encode("latin-1"), "text/html; charset=latin-1")
