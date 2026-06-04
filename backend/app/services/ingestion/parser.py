"""Pure HTML → text extraction (no I/O, no dependencies).

v1 handles HTML only, using the stdlib ``html.parser`` (no new dependency).
``beautifulsoup4`` / ``trafilatura`` are a future *quality* upgrade (better
boilerplate stripping), not a v1 need — see ADR 0008. Non-HTML content raises
`ParseError`, which the `IngestionService` catches per-source.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags whose text content is not human-readable body content.
_SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg"}
_WS = re.compile(r"\s+")


class ParseError(RuntimeError):
    """Raised when content cannot be parsed as HTML text."""


class _TextExtractor(HTMLParser):
    """Collects visible text, skipping non-content tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # entities arrive unescaped
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def _charset(content_type: str | None) -> str:
    if content_type and "charset=" in content_type.lower():
        return content_type.lower().split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    return "utf-8"


def parse_html(content: bytes, content_type: str | None = None) -> str:
    """Extract collapsed visible text from HTML bytes.

    Raises `ParseError` if ``content_type`` is present and is clearly not HTML.
    """
    if content_type and "html" not in content_type.lower() and "text" not in content_type.lower():
        raise ParseError(f"unsupported content-type for HTML parser: {content_type!r}")
    try:
        html = content.decode(_charset(content_type), errors="replace")
    except (LookupError, UnicodeDecodeError) as exc:  # unknown charset, etc.
        raise ParseError(f"could not decode content: {exc}") from exc
    extractor = _TextExtractor()
    extractor.feed(html)
    return _WS.sub(" ", extractor.text).strip()
