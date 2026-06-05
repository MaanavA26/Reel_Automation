"""Deterministic de-dupe + ranking of candidate topic ideas.

Per CLAUDE.md §4 this is a *tool*, not an agent: a pure, explainable, fully
deterministic transform from raw `TopicIdea` candidates (possibly fanned out
across niches/providers) into a prioritized list ready for the scheduler's topic
queue. No judgment, no LLM — the "which topic do we green-light" decision belongs
to a future content-strategy agent that consumes this ordered output.

The ranking signal is intentionally simple and reviewable: sort by each idea's
provider-authored `signal` (higher is hotter), descending. Ties — and ideas with
no signal — are broken by normalized title then id, so ordering never depends on
input order or a stable-sort accident (which would make tests flaky). De-dupe
collapses ideas that normalize to the same key (lowercased/whitespace-collapsed
keyword, falling back to title), keeping the highest-signal idea *wholesale* (its
id, its provenance) — sources/scores are never merged (that would overbuild
"simple" and blur provenance).
"""

from __future__ import annotations

import re

from app.topics.base import TopicIdea

_WHITESPACE = re.compile(r"\s+")

# Ideas with no provider signal sort below every signalled idea (the advisor's
# "None ranks lowest" rule), without special-casing the comparator.
_NO_SIGNAL_RANK = float("-inf")


def _normalize_key(idea: TopicIdea) -> str:
    """A stable de-dupe key: lowercased, whitespace-collapsed keyword|title.

    Prefers `keyword` (the provider's canonical term) and falls back to `title`
    so ideas with no keyword still de-dupe by their displayed text.
    """
    raw = idea.keyword or idea.title
    return _WHITESPACE.sub(" ", raw.strip().lower())


def _rank_key(idea: TopicIdea) -> tuple[float, str, str]:
    """Sort key: signal desc, then title asc, then id asc (deterministic).

    Returned for use with ``reverse=False`` by negating the signal, so a higher
    signal sorts first while the string tie-breaks stay ascending.
    """
    signal = -idea.signal if idea.signal is not None else -_NO_SIGNAL_RANK
    return (signal, _WHITESPACE.sub(" ", idea.title.strip().lower()), idea.id)


def select_topics(candidates: list[TopicIdea], *, limit: int | None = None) -> list[TopicIdea]:
    """De-dupe and rank candidate topics into a prioritized list.

    Args:
        candidates: raw topic ideas, possibly with duplicates across providers.
        limit: optional cap on the returned list (the scheduler's queue depth);
            ``None`` returns all de-duped ideas.

    Returns:
        A new list ordered best-first by the documented ranking signal, with at
        most one idea per normalized key. Never mutates the input.
    """
    best_by_key: dict[str, TopicIdea] = {}
    for idea in candidates:
        key = _normalize_key(idea)
        incumbent = best_by_key.get(key)
        if incumbent is None or _rank_key(idea) < _rank_key(incumbent):
            # `_rank_key` is best-first ascending, so the "smaller" key wins —
            # higher signal, else lexically-first title/id — keeping de-dupe and
            # ranking consistent (the kept idea is the one that would rank first).
            best_by_key[key] = idea

    ranked = sorted(best_by_key.values(), key=_rank_key)
    return ranked[:limit] if limit is not None else ranked
