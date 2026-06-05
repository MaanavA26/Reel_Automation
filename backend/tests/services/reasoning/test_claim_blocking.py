"""Tests for the deterministic claim-blocking tool (M8).

Pins the contract the Cross-Verification agent relies on: blocks partition the
evidence indices, lexical overlap groups related claims, unrelated claims become
singletons, oversized components are capped, and the output is fully
deterministic (stable order) so `FakeProvider` can script one response per block.
"""

from __future__ import annotations

from app.schemas.research_state import Evidence
from app.services.reasoning.claim_blocking import build_claim_blocks


def _ev(claim: str, source_id: str = "src_a") -> Evidence:
    return Evidence(
        claim=claim,
        source_id=source_id,
        source_url="https://a.com",
        chunk_id="chk_1",
        chunk_text="…",
        confidence=0.8,
        extracted_via="extraction:fake",
    )


def test_empty_input_returns_no_blocks() -> None:
    assert build_claim_blocks([]) == []


def test_overlapping_claims_group_together() -> None:
    evidence = [
        _ev("Fusion reactors require extreme temperatures"),
        _ev("Extreme temperatures are needed for fusion reactors"),
        _ev("Penguins live in Antarctica"),
    ]
    blocks = build_claim_blocks(evidence)
    # The two fusion claims share salient tokens → one block; penguins alone.
    assert [0, 1] in blocks
    assert [2] in blocks
    assert len(blocks) == 2


def test_unrelated_claims_become_singletons() -> None:
    evidence = [_ev("Quantum entanglement links particles"), _ev("Volcanoes erupt molten rock")]
    blocks = build_claim_blocks(evidence)
    assert blocks == [[0], [1]]


def test_blocks_partition_all_indices() -> None:
    evidence = [_ev(c) for c in ("alpha beta", "beta gamma", "delta", "epsilon zeta", "zeta eta")]
    blocks = build_claim_blocks(evidence)
    flattened = sorted(i for block in blocks for i in block)
    assert flattened == list(range(len(evidence)))


def test_deterministic_order() -> None:
    evidence = [_ev(c) for c in ("solar power output", "wind power output", "tidal patterns")]
    assert build_claim_blocks(evidence) == build_claim_blocks(evidence)


def test_block_size_is_capped() -> None:
    # Five claims all sharing the same salient token form one component; a
    # max_block of 2 splits it into deterministic contiguous sub-blocks.
    evidence = [_ev(f"renewable energy fact number {i}") for i in range(5)]
    blocks = build_claim_blocks(evidence, max_block=2)
    assert all(len(block) <= 2 for block in blocks)
    assert sorted(i for block in blocks for i in block) == [0, 1, 2, 3, 4]


def test_min_shared_tokens_threshold() -> None:
    # Sharing only one salient token does not group when two are required.
    evidence = [_ev("solar energy efficiency"), _ev("solar panel manufacturing")]
    assert build_claim_blocks(evidence, min_shared_tokens=2) == [[0], [1]]
    # Two shared salient tokens ("solar", "energy") do group.
    evidence2 = [_ev("solar energy efficiency"), _ev("solar energy storage")]
    assert build_claim_blocks(evidence2, min_shared_tokens=2) == [[0, 1]]


def test_stopwords_do_not_group_unrelated_claims() -> None:
    # Both claims share only stopwords ("the", "is", "a") → must NOT group.
    evidence = [_ev("the cat is a mammal"), _ev("the result is a success")]
    assert build_claim_blocks(evidence) == [[0], [1]]
