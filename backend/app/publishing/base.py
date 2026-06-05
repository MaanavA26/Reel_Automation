"""Provider-neutral contract for the publishing / social-ops band.

A `PublishingProvider` takes a finished `RenderedVideo` (the Media Production
layer's output artifact; ADR 0019) plus a `PublishTarget` (the platform-facing
metadata) and uploads it to a platform, returning a `PublishResult` that carries
the platform-minted post id and url.

Per CLAUDE.md §4 this is deterministic *tool/service* work (API wrappers, I/O),
not an agent: the upstream agentic layer decides *what* to publish and *when*;
the provider *executes* the upload. The provider — never an LLM — is the only
thing that mints a real platform post id/url, the publishing-side analogue of the
search fabric minting a real `Source.url` (CLAUDE.md §11; the evidence/provenance
boundary made structural). All DTOs live here beside the protocol, mirroring the
search fabric (`SearchResult` in `search/base.py`) and the visuals band; the
Media `schemas.py` is intentionally not touched.

This module imports `RenderedVideo` from `app.media.schemas`: that is the
*intended consumption* of the artifact (a read of a published contract), the
same single deliberate cross-layer import the media pipeline makes of
`CreatorPacket` — not a write to another band. See ADR 0033 §coupling.
"""

from __future__ import annotations

import secrets
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.media.schemas import RenderedVideo

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy; hex-only suffix keeps the underscore prefix-delimiter
    # unambiguous. Same scheme as `research_state._gen_id` / `media.schemas._gen_id`,
    # copied (not imported) to keep this band decoupled from those layers — the
    # ADR 0019-blessed copy-not-import convention applied here. See ADR 0033.
    return f"{prefix}_{secrets.token_hex(8)}"


class PublishTarget(BaseModel):
    """Platform-facing metadata for one publish (the "what to post" payload).

    Minimal and load-bearing only (CLAUDE.md §7 — no speculative fields):
    everything a short-form upload needs. `privacy_status` mirrors the platform
    vocabulary (``public`` / ``unlisted`` / ``private``) and defaults to
    ``private`` so an un-set target never accidentally posts publicly. Tags are
    optional. Hashtag/Shorts signalling is the adapter's concern, not this DTO's.
    """

    model_config = _STRICT

    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy_status: str = "private"


class PublishResult(BaseModel):
    """The outcome of a successful publish — a platform-minted handle.

    Strict (`extra='forbid'`), id-prefixed (``pub_``), and carrying a required
    `published_via` provenance string (``"publish:youtube"`` / ``"publish:fake"``),
    symmetric with `RenderedVideo.produced_via` and `Source.discovered_via`
    (CLAUDE.md §11; ADR 0006/0019). `post_id` and `url` are authored by the
    platform, never inferred — the publishing-side evidence boundary.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("pub"))
    platform: str
    post_id: str
    url: str
    published_via: str


@runtime_checkable
class PublishingProvider(Protocol):
    """A publishing backend that uploads a rendered video to a platform.

    Async to match the repo's I/O-bound provider contract (ADR 0002/0003) —
    real publishing is a network upload. Implementations wrap a platform's
    upload API and return a `PublishResult`; the concrete YouTube adapter is
    `YouTubeShortsPublisher`, with TikTok / Instagram skeletons pending.
    """

    name: str

    async def publish(self, *, video: RenderedVideo, target: PublishTarget) -> PublishResult: ...


class FakePublishingProvider:
    """A hermetic `PublishingProvider` for offline tests (no network).

    Returns a deterministic `PublishResult` with a synthetic post id/url and
    records each call for assertions. Mirrors `FakeSearchProvider` /
    `FakeTTSProvider` (factory-style fake; testing-standards "fake, don't mock").
    """

    name = "fake"

    def __init__(self, *, platform: str = "fake") -> None:
        self._platform = platform
        self.calls: list[tuple[RenderedVideo, PublishTarget]] = []

    async def publish(self, *, video: RenderedVideo, target: PublishTarget) -> PublishResult:
        self.calls.append((video, target))
        post_id = f"fakepost{len(self.calls)}"
        return PublishResult(
            platform=self._platform,
            post_id=post_id,
            url=f"fake://{self._platform}/{post_id}",
            published_via=f"publish:{self.name}",
        )
