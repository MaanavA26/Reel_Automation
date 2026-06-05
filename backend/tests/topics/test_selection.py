"""Tests for the deterministic `select_topics` de-dupe + ranking tool."""

from __future__ import annotations

from app.topics.base import TopicIdea
from app.topics.selection import select_topics


def _idea(title: str, *, signal: float | None = None, keyword: str | None = None) -> TopicIdea:
    return TopicIdea(title=title, sourced_via="trends:fake", signal=signal, keyword=keyword)


def test_ranks_by_signal_descending() -> None:
    out = select_topics(
        [_idea("low", signal=10), _idea("high", signal=90), _idea("mid", signal=50)]
    )
    assert [i.title for i in out] == ["high", "mid", "low"]


def test_none_signal_ranks_lowest() -> None:
    out = select_topics([_idea("nosig"), _idea("sig", signal=1)])
    assert [i.title for i in out] == ["sig", "nosig"]


def test_signal_ties_break_by_title_then_id() -> None:
    # Same signal, distinct titles -> deterministic alphabetical order.
    out = select_topics([_idea("banana", signal=5), _idea("apple", signal=5)])
    assert [i.title for i in out] == ["apple", "banana"]


def test_dedupes_by_normalized_keyword_keeping_highest_signal() -> None:
    weak = _idea("AI Agents", keyword="ai agents", signal=10)
    strong = _idea("AI agents trend", keyword="  AI   AGENTS ", signal=80)
    out = select_topics([weak, strong])
    assert len(out) == 1
    assert out[0].id == strong.id  # kept wholesale, not merged


def test_dedupe_falls_back_to_title_when_no_keyword() -> None:
    out = select_topics([_idea("Same Topic", signal=1), _idea("same   topic", signal=5)])
    assert len(out) == 1
    assert out[0].signal == 5


def test_limit_caps_output() -> None:
    out = select_topics([_idea(t, signal=i) for i, t in enumerate("abcde")], limit=2)
    assert len(out) == 2


def test_is_deterministic_across_input_order() -> None:
    a = _idea("x", keyword="x", signal=5)
    b = _idea("y", keyword="y", signal=5)
    c = _idea("z", keyword="z", signal=9)
    assert [i.id for i in select_topics([a, b, c])] == [i.id for i in select_topics([c, b, a])]


def test_does_not_mutate_input() -> None:
    candidates = [_idea("a", signal=1), _idea("b", signal=2)]
    snapshot = list(candidates)
    select_topics(candidates)
    assert candidates == snapshot


def test_empty_input_returns_empty() -> None:
    assert select_topics([]) == []
