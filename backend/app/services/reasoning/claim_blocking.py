"""Deterministic claim blocking — bounds the cross-verification cross-product.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM, no network):
given the band's `Evidence`, it groups claims that *might* be about the same
thing into candidate **blocks** so the Cross-Verification agent (M8) judges one
bounded, related cluster per model call instead of comparing all N claims
pairwise (O(N²)). It deliberately *over-groups* by cheap lexical overlap — the
agent does the semantic adjudication of which members truly corroborate or
contradict within a block (the deterministic-floor / judgment-ceiling split).

Determinism is part of the contract: identical input yields identical blocks in
identical order (stable sort, fixed tie-breaks), so the number and order of
downstream model calls is fixed per input — which is what lets the hermetic
`FakeProvider` tests script one response per block. See ADR 0010.
"""

from __future__ import annotations

import re

from app.schemas.research_state import Evidence

# A small stdlib stopword set: common words carry no topical signal, so blocking
# on them would collapse unrelated claims into one giant block. Kept minimal and
# inline (no new dependency); it is a relevance heuristic, not linguistics.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "as",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "has",
        "have",
        "had",
        "not",
        "no",
        "than",
        "then",
        "there",
        "their",
        "they",
        "can",
        "could",
        "will",
        "would",
        "may",
        "might",
        "should",
        "must",
        "into",
        "over",
        "under",
        "about",
        "more",
        "most",
        "such",
        "also",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 3


def _salient_tokens(text: str) -> set[str]:
    """Lowercase, tokenize, and drop stopwords / very short tokens.

    Returns the set of topical tokens used to decide block membership.
    """
    return {
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS
    }


class _DisjointSet:
    """Minimal union-find over evidence indices (deterministic, stdlib only)."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression keeps later lookups cheap; does not affect grouping.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Attach larger index under smaller so component roots are stable.
            self._parent[max(ra, rb)] = min(ra, rb)


def build_claim_blocks(
    evidence: list[Evidence],
    *,
    min_shared_tokens: int = 1,
    max_block: int = 12,
) -> list[list[int]]:
    """Group evidence into candidate blocks of indices into ``evidence``.

    Two evidence items are linked when their claims share at least
    ``min_shared_tokens`` salient tokens; linked items form a connected
    component (a block). Items sharing nothing become singleton blocks, so the
    return value is always a *partition* of ``range(len(evidence))`` — every
    index appears in exactly one block. Oversized components are split into
    deterministic contiguous sub-blocks of at most ``max_block`` items, bounding
    the context (and cost) of any single downstream model call.

    Blocks are returned sorted by their smallest member index, and indices
    within each block are ascending — a fully deterministic ordering.
    """
    n = len(evidence)
    if n == 0:
        return []

    token_sets = [_salient_tokens(ev.claim) for ev in evidence]

    # Inverted index token -> indices, to find co-occurring (candidate) pairs
    # without an all-pairs scan over unrelated claims.
    inverted: dict[str, list[int]] = {}
    for idx, tokens in enumerate(token_sets):
        for tok in tokens:
            inverted.setdefault(tok, []).append(idx)

    # Count shared salient tokens only for pairs that co-occur on some token,
    # then union pairs meeting the threshold.
    shared: dict[tuple[int, int], int] = {}
    for indices in inverted.values():
        for i_pos in range(len(indices)):
            for j_pos in range(i_pos + 1, len(indices)):
                pair = (indices[i_pos], indices[j_pos])
                shared[pair] = shared.get(pair, 0) + 1

    dsu = _DisjointSet(n)
    for (a, b), count in shared.items():
        if count >= min_shared_tokens:
            dsu.union(a, b)

    # Gather components, keeping member indices ascending.
    components: dict[int, list[int]] = {}
    for idx in range(n):
        components.setdefault(dsu.find(idx), []).append(idx)

    blocks: list[list[int]] = []
    for members in components.values():
        members.sort()
        # Split oversized components into deterministic contiguous sub-blocks.
        for start in range(0, len(members), max_block):
            blocks.append(members[start : start + max_block])

    blocks.sort(key=lambda block: block[0])
    return blocks
