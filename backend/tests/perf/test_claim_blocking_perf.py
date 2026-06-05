"""Benchmark: ``build_claim_blocks`` scaling across input sizes (offline).

``build_claim_blocks`` (the M8 deterministic claim-blocking *tool*) bounds the
Cross-Verification cross-product: it groups lexically-overlapping claims so the
agent judges one cluster per model call instead of comparing all N claims
pairwise. Its co-occurrence pass is worst-case O(N²) within a token bucket, so
its scaling with input size is the thing worth watching as the evidence corpus
grows — this benchmark measures it at a few sizes and prints a timing table.

Marked ``@pytest.mark.integration`` so it is **deselected from the default
suite** (the project's ``addopts = "-m 'not integration'"`` does this) — *not*
because it needs network or credentials (it is fully offline and deterministic),
but because the project's ``pyproject.toml`` (where a dedicated default-deselect
marker would be registered) is out of scope for this component, and ``integration``
is the existing lever for "do not run in the default suite". It also carries
``@pytest.mark.perf`` (registered in this package's ``conftest``) as the
*positive* selector: ``pytest -m perf`` runs only the benchmarks and excludes the
network-gated live tests a bare ``-m integration`` would pull in. These are
informational timings, not pass/fail gates — the only assertions are structural
sanity checks (the result is a valid partition), never a latency threshold.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.schemas.research_state import Evidence
from app.services.reasoning.claim_blocking import build_claim_blocks
from tests.perf.harness import TimingResult, render_table, time_callable

# A small deterministic topical vocabulary. Claims are built by mixing a few
# *shared* tokens (so items genuinely link into multi-member blocks and exercise
# the inverted-index + union-find path) with a per-item *unique* token (so the
# corpus does not collapse into one giant block). All-disjoint or all-identical
# inputs would not exercise the O(N²)-bounded co-occurrence pass this measures.
_TOPICS = (
    "quantum",
    "neural",
    "climate",
    "genome",
    "reactor",
    "satellite",
    "protein",
    "lattice",
)

_INPUT_SIZES = (50, 200, 800)


def _make_evidence(n: int) -> list[Evidence]:
    """Build ``n`` deterministic Evidence items with partial token overlap.

    Each claim shares a rotating topic token with its neighbours (so blocks form)
    and carries a unique ``itemNNNN`` token (so blocks stay bounded). Provenance
    fields are placeholders — claim text is all ``build_claim_blocks`` reads.
    """
    evidence: list[Evidence] = []
    for i in range(n):
        topic = _TOPICS[i % len(_TOPICS)]
        neighbour = _TOPICS[(i // len(_TOPICS)) % len(_TOPICS)]
        claim = f"the {topic} {neighbour} measurement shows effect item{i:04d}"
        evidence.append(
            Evidence(
                claim=claim,
                source_id=f"src-{i}",
                source_url=f"https://example.test/{i}",
                chunk_id=f"chunk-{i}",
                chunk_text=claim,
                confidence=0.5,
                extracted_via="perf:benchmark",
            )
        )
    return evidence


def _assert_partition(blocks: list[list[int]], n: int) -> None:
    """Sanity check: the blocks are a partition of ``range(n)``."""
    flattened = sorted(idx for block in blocks for idx in block)
    assert flattened == list(range(n)), "build_claim_blocks did not return a partition"


@pytest.mark.integration
@pytest.mark.perf
def test_build_claim_blocks_scaling(record_perf_table: Callable[[str], None]) -> None:
    """Time ``build_claim_blocks`` at a few input sizes; print a timing table."""
    results: list[TimingResult] = []
    for size in _INPUT_SIZES:
        evidence = _make_evidence(size)
        result, blocks = time_callable(
            f"build_claim_blocks(N={size})",
            lambda evidence=evidence: build_claim_blocks(evidence),
            repeats=5,
        )
        _assert_partition(blocks, size)
        results.append(result)

    record_perf_table(render_table("build_claim_blocks scaling", results))
