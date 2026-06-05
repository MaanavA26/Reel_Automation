"""In-memory fakes for hermetic ingestion tests (no network, no ``pypdf``).

`FakeFetchProvider` replays scripted `FetchedContent` per URL (mirrors
`app.services.search.fakes.FakeSearchProvider`); `FakePdfParser` replays scripted
text per PDF bytes so the PDF route is testable without the real dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.ingestion.base import FetchedContent, FetchError
from app.services.ingestion.parser import ParseError


@dataclass
class RecordedFetch:
    """A single ``fetch`` invocation captured by the fake."""

    url: str


class FakeFetchProvider:
    """A `FetchProvider` that returns scripted content keyed by URL.

    Construct with a mapping of url → `FetchedContent`. An unmapped URL raises
    `FetchError`, so tests can exercise the per-source skip path.
    """

    name = "fake"

    def __init__(self, by_url: Mapping[str, FetchedContent] | None = None) -> None:
        self._by_url: dict[str, FetchedContent] = dict(by_url or {})
        self.calls: list[RecordedFetch] = []

    async def fetch(self, *, url: str) -> FetchedContent:
        self.calls.append(RecordedFetch(url=url))
        if url not in self._by_url:
            raise FetchError(f"no scripted content for {url!r}")
        return self._by_url[url]


class FakePdfParser:
    """A `PdfParser` that returns scripted text keyed by raw PDF bytes.

    Construct with a mapping of bytes → extracted text. Unmapped bytes raise
    `ParseError`, so tests can exercise the per-source skip path without ``pypdf``.
    """

    name = "fake-pdf"

    def __init__(self, by_bytes: Mapping[bytes, str] | None = None) -> None:
        self._by_bytes: dict[bytes, str] = dict(by_bytes or {})

    def parse(self, content: bytes) -> str:
        if content not in self._by_bytes:
            raise ParseError(f"no scripted text for {len(content)} bytes")
        return self._by_bytes[content]
