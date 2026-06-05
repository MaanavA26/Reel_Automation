"""Pure text → Chunk[] chunking (no I/O).

v1 uses a deterministic fixed-size character window with overlap — bounded chunk
sizes (stable for the downstream M7 extraction agent's context budget and future
embeddings) and trivially testable. Token-aware / semantic chunking is deferred
to its consumer (M7 reveals the desired size); see ADR 0008.
"""

from __future__ import annotations

from app.schemas.research_state import Chunk

DEFAULT_WINDOW = 1200
DEFAULT_OVERLAP = 200


def chunk_text(
    text: str,
    *,
    source_id: str,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split ``text`` into overlapping `Chunk`s linked to ``source_id``.

    ``position`` is the 0-based ordinal of the chunk within its source. Ids and
    timestamps are schema-minted. Empty/whitespace text yields no chunks.
    """
    if window <= 0 or overlap < 0 or overlap >= window:
        raise ValueError("require window > 0 and 0 <= overlap < window")
    cleaned = text.strip()
    if not cleaned:
        return []

    step = window - overlap
    chunks: list[Chunk] = []
    position = 0
    start = 0
    while True:
        piece = cleaned[start : start + window]
        chunks.append(Chunk(source_id=source_id, text=piece, position=position))
        position += 1
        # Stop once a chunk reaches the end of the text: the next window would
        # span [start + step, len) ⊂ [start, len) — wholly contained in this
        # chunk's overlap region — so it would add no new content.
        if start + window >= len(cleaned):
            break
        start += step
    return chunks
