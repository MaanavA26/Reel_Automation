"""Provider-neutral contract for visual / B-roll retrieval.

A `VisualProvider` turns a query (a keyword or short phrase derived from the
script) into candidate `VisualClip`s — the short background/B-roll clips and
stills the composition step (`CompositionService.render(..., visual_uris=...)`,
ADR 0019) lays under the narration and captions. Per CLAUDE.md §3.3/§4 this is
deterministic *tool/service* work ("image/video generation or retrieval"): the
upstream Short-Form Content Strategist decides *what* the visuals should depict;
the provider *executes* the retrieval. The provider — never an LLM — is the only
thing that mints a real asset `uri`, mirroring how the search fabric keeps the
evidence-vs-inference boundary structural (CLAUDE.md §11; ADR 0006).

Async to match the repo's I/O-bound provider contract (ADR 0002/0003) — real
retrieval is a network call to a stock-media vendor. Each `VisualClip` carries a
required `produced_via` provenance string (`"visuals:fake"`, `"visuals:stock"`),
symmetric with `SynthesizedSpeech.produced_via` / `Source.discovered_via`
(ADR 0006/0019), so an artifact always records which tool retrieved it.

This module ships the protocol + DTO + a hermetic `FakeVisualProvider`. The real
`StockVisualProvider` adapter lives alongside in `stock.py`. The DTO lives here
(not in `app.media.schemas`) so the band is self-contained, mirroring the search
fabric where `SearchResult` sits in `search/base.py` beside its protocol; the
`_gen_id` helper is a small local copy (not a cross-layer import of a private
symbol), the same copy-not-import move ADR 0019 blessed for this layer.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8); hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous. Same scheme as ADR 0001's
    # `research_state._gen_id`, copied (not imported) to keep the layer
    # decoupled — see this module's docstring and ADR 0019.
    return f"{prefix}_{secrets.token_hex(8)}"


class VisualKind(StrEnum):
    """Whether a retrieved asset is a moving clip or a still image.

    Load-bearing for the B-roll use case: the composition step holds a still for
    a caption's duration but plays a clip on its own timeline, and only a
    ``VIDEO`` carries a meaningful `duration_ms`.
    """

    VIDEO = "video"
    IMAGE = "image"


class VisualClip(BaseModel):
    """A single retrieved visual asset — a lightweight descriptor, not the bytes.

    Like the other media artifacts (`SynthesizedSpeech`, `RenderedVideo`) this
    points at where the asset lives (`uri`) and carries the metadata the
    composition step needs to lay it under the narration; the bytes are an opaque
    blob owned by the vendor/storage. `duration_ms` is ``None`` for a still image
    (`VisualKind.IMAGE`). `attribution` records the author credit some stock
    licenses require; it is provider-authored and optional. Strict
    (`extra='forbid'`), id-prefixed (`vis_`), with a required `produced_via`.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("vis"))
    uri: str
    kind: VisualKind
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, ge=0)
    attribution: str | None = None
    produced_via: str


@runtime_checkable
class VisualProvider(Protocol):
    """A backend that retrieves candidate visual clips/images for a query.

    Implementations wrap a stock-media API or generator and return `VisualClip`
    descriptors (the asset bytes are streamed by the vendor/composition step; the
    layer traffics in descriptors). Async — real retrieval is network I/O.
    """

    name: str

    async def search(self, *, query: str, limit: int = 10) -> list[VisualClip]: ...


@dataclass
class RecordedVisualSearch:
    """A single `search` invocation captured by the fake."""

    query: str
    limit: int


class FakeVisualProvider:
    """A hermetic `VisualProvider` for offline tests (no network, no bytes).

    A factory-style fake (testing-standards: "don't mock what you can fake") that
    returns scripted clips per query and records the queries it received.
    Construct with either a flat list of clips (returned for every query) or a
    per-query mapping. Returns at most ``limit`` clips, mirroring a real backend.
    Mirrors `app.services.search.fakes.FakeSearchProvider`.
    """

    name = "fake"

    def __init__(
        self,
        clips: Sequence[VisualClip] | None = None,
        *,
        by_query: Mapping[str, Sequence[VisualClip]] | None = None,
    ) -> None:
        self._clips: list[VisualClip] = list(clips or [])
        self._by_query: dict[str, list[VisualClip]] = {
            q: list(cs) for q, cs in (by_query or {}).items()
        }
        self.calls: list[RecordedVisualSearch] = []

    async def search(self, *, query: str, limit: int = 10) -> list[VisualClip]:
        self.calls.append(RecordedVisualSearch(query=query, limit=limit))
        hits = self._by_query.get(query, self._clips)
        return list(hits[:limit])
