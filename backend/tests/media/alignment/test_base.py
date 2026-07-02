"""Tests for the `WordAligner` seam: tokenization and the hermetic fake (ADR 0062)."""

from __future__ import annotations

import asyncio

from app.media.alignment.base import (
    FakeWordAligner,
    WordAligner,
    split_words,
)

# --- split_words (the single shared tokenization rule) -----------------------


def test_split_words_is_whitespace_tokenization() -> None:
    # Punctuation stays attached to its word; runs of whitespace collapse.
    assert split_words("Hello,  brave world!") == ["Hello,", "brave", "world!"]


def test_split_words_empty_and_blank() -> None:
    assert split_words("") == []
    assert split_words("   \t ") == []


# --- FakeWordAligner ----------------------------------------------------------


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeWordAligner(), WordAligner)


def test_fake_returns_per_segment_spans_on_one_running_clock() -> None:
    aligner = FakeWordAligner(ms_per_word=100)
    result = asyncio.run(
        aligner.align(audio_path="fake://tts/a.wav", segments=["one two", "three"])
    )
    assert len(result) == 2
    assert [w.text for w in result[0]] == ["one", "two"]
    assert [w.text for w in result[1]] == ["three"]
    # One running clock across segments: 0-100, 100-200, then 200-300.
    assert [(w.start_ms, w.end_ms) for w in result[0]] == [(0, 100), (100, 200)]
    assert [(w.start_ms, w.end_ms) for w in result[1]] == [(200, 300)]


def test_fake_is_deterministic() -> None:
    a = asyncio.run(FakeWordAligner().align(audio_path="x", segments=["a b", "c"]))
    b = asyncio.run(FakeWordAligner().align(audio_path="x", segments=["a b", "c"]))
    assert a == b


def test_fake_records_calls() -> None:
    aligner = FakeWordAligner()
    asyncio.run(aligner.align(audio_path="file:///tmp/n.wav", segments=["hi there"]))
    assert len(aligner.calls) == 1
    assert aligner.calls[0].audio_path == "file:///tmp/n.wav"
    assert aligner.calls[0].segments == ["hi there"]


def test_fake_empty_segment_yields_empty_span_list() -> None:
    result = asyncio.run(FakeWordAligner().align(audio_path="x", segments=["", "word"]))
    assert result[0] == []
    assert [w.text for w in result[1]] == ["word"]
