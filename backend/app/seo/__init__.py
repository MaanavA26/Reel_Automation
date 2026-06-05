"""SEO metadata generation for the publishing-support surface.

Turns a Deep Research `CreatorPacket` (+ its source `Report`) into the
discovery-oriented title / description / tags / hashtags an upload needs
(CLAUDE.md §3.4 publishing support, §5.4 creator packet → downstream handoff).
Per CLAUDE.md §4 this is a deterministic **tool**, never an agent: it is a pure
value derivation over already-synthesized creative material, so it is fully
equality-testable with no LLM and no I/O. LLM-polished copy is a documented
future enhancement (see `metadata.MetadataBuilder` and ADR 0039).
"""

from __future__ import annotations

from app.seo.metadata import (
    MetadataBuilder,
    MetadataError,
    VideoMetadata,
)

__all__ = [
    "MetadataBuilder",
    "MetadataError",
    "VideoMetadata",
]
