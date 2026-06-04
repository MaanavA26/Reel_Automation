"""Tests for the pure text chunker (M6)."""

from __future__ import annotations

import pytest

from app.services.ingestion.chunker import chunk_text


def test_windows_overlap_positions_and_source_link() -> None:
    chunks = chunk_text("x" * 2500, source_id="src_1", window=1000, overlap=200)
    # step = 800 → starts 0, 800, 1600, 2400 → 4 chunks
    assert [c.position for c in chunks] == [0, 1, 2, 3]
    assert all(c.source_id == "src_1" for c in chunks)
    assert len(chunks[0].text) == 1000
    assert len(chunks[-1].text) == 100  # 2500 - 2400


def test_short_text_single_chunk() -> None:
    chunks = chunk_text("hello", source_id="s")
    assert len(chunks) == 1
    assert chunks[0].text == "hello"
    assert chunks[0].position == 0


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("   \n  ", source_id="s") == []


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", source_id="s", window=10, overlap=10)
