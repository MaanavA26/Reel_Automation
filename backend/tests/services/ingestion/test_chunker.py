"""Tests for the pure text chunker (M6)."""

from __future__ import annotations

import pytest

from app.services.ingestion.chunker import chunk_text


def test_windows_overlap_positions_and_source_link() -> None:
    chunks = chunk_text("x" * 2500, source_id="src_1", window=1000, overlap=200)
    # step = 800 → starts 0, 800, 1600; start=1600 chunk [1600:2500] already
    # reaches the end, so no redundant [2400:2500] tail is emitted → 3 chunks.
    assert [c.position for c in chunks] == [0, 1, 2]
    assert all(c.source_id == "src_1" for c in chunks)
    assert len(chunks[0].text) == 1000
    assert len(chunks[-1].text) == 900  # 2500 - 1600


def test_no_duplicate_tail_when_remainder_within_overlap() -> None:
    # window + 1 input where overlap >= window/2: a naive `start < len` loop
    # emits a redundant final chunk wholly contained in the previous one.
    text = "".join(chr(ord("a") + (i % 26)) for i in range(11))  # window + 1
    window, overlap, step = 10, 6, 4
    chunks = chunk_text(text, source_id="s", window=window, overlap=overlap)
    # Old loop: [0:10], [4:11], then start=8 < 11 → [8:11] ⊂ [4:11] (redundant
    # tail). Fixed loop: [0:10]; [4:11] reaches the end → stop. 2 chunks, no tail.
    assert [c.position for c in chunks] == [0, 1]
    # No characters lost: the union of the chunks' covered ranges is the input.
    covered = bytearray(len(text))
    start = 0
    for c in chunks:
        end = min(start + window, len(text))
        assert c.text == text[start:end]
        for i in range(start, end):
            covered[i] = 1
        start += step
    assert all(covered)


def test_chunks_cover_full_input_without_loss() -> None:
    # Distinct characters per index let us prove no content is dropped: the
    # union of [start, end) ranges must reconstruct the exact input.
    text = "".join(chr(ord("A") + (i % 26)) for i in range(2500))
    window, overlap, step = 1000, 200, 800
    chunks = chunk_text(text, source_id="s", window=window, overlap=overlap)

    covered = bytearray(len(text))
    start = 0
    for c in chunks:
        end = min(start + window, len(text))
        for i in range(start, end):
            covered[i] = 1
        # Each chunk's text matches the slice it claims to cover.
        assert c.text == text[start:end]
        start += step
    assert all(covered), "every input character must be covered by some chunk"


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
