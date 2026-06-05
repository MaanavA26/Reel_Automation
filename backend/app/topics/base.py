"""Provider-neutral contract for the topic / trend sourcing fabric.

A `TrendProvider` turns a niche/seed into candidate trending `TopicIdea`s. Per
CLAUDE.md §4 this is deterministic *tool/service* work (API-wrapping); a future
content-strategy agent decides *which* niche to mine and *which* surfaced topic
to green-light — the provider only *executes* the discovery.

`TopicIdea` is deliberately `Source`-shaped, **not** `SearchResult`-shaped. The
search fabric keeps provenance *off* its thin `SearchResult` DTO and mints it
only when a hit is promoted to a persisted `Source`. A `TopicIdea` is itself the
persisted artifact handed to the scheduler, so — like `Source` — it carries an
auto-minted id and required provenance inline. That `sourced_via` is the §11
evidence-vs-inference anchor: a candidate topic is always *tool-discovered*
(symmetric with `Source.discovered_via`), never invented by an LLM as if it were
an established trend. See ADR 0037.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


def _gen_id(prefix: str) -> str:
    # Local copy of the schema layer's id scheme (ADR 0001). Topics is a §3.4
    # future layer independent of Deep Research; following the media-layer
    # precedent (ADR 0019) it keeps a local copy rather than importing from
    # `app.schemas`, so the package builds/tests/showcases standalone.
    return f"{prefix}_{secrets.token_hex(8)}"


_STRICT = ConfigDict(extra="forbid")


class TopicIdea(BaseModel):
    """A candidate trending topic surfaced by a `TrendProvider`.

    The `signal` is a provider-authored, *higher-is-hotter* relevance/popularity
    score (e.g. normalized search volume or growth from a trends API). Its
    absolute scale is provider-specific; `selection.py` only compares signals
    *within a single candidate set*, so cross-provider scale differences are not
    a concern here. ``None`` means "the provider reported no signal" and ranks
    lowest (see `select_topics`).
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("topic"))
    title: str
    sourced_via: str
    niche: str | None = None
    keyword: str | None = None
    signal: float | None = None
    url: str | None = None
    sourced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_metadata: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class TrendProvider(Protocol):
    """A trends backend that returns candidate topic ideas for a niche/seed.

    Async to match the rest of the fabric (search/LLM) — real trend sourcing is
    network I/O. Implementations wrap a trends/keyword API and are the only thing
    that mints a real `sourced_via`/`url`, keeping the §11 boundary structural.
    """

    name: str

    async def discover(self, *, niche: str, limit: int = 10) -> list[TopicIdea]: ...
