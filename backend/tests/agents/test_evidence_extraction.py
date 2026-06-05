"""Tests for the Evidence Extraction agent (M7).

Hermetic: a `FakeProvider` scripts one `_ExtractionOutput` per chunk-call. The
tests pin the contract: provenance is code-attached from the Chunk/Source (never
the model), confidence passes through, `extracted_via` carries the model id,
per-chunk empties are tolerated, and zero-total / unknown-source raise.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.evidence_extraction import (
    EvidenceExtractionAgent,
    ExtractionError,
    _ExtractedClaim,
    _ExtractionOutput,
)
from app.schemas.research_state import Chunk, Source, SourceType
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _source() -> Source:
    return Source(url="https://a.com", type=SourceType.WEB, discovered_via="search:fake")


def _agent(outputs: list[_ExtractionOutput]) -> tuple[EvidenceExtractionAgent, FakeProvider]:
    fake = FakeProvider(outputs)
    router = ModelRouter(
        providers={"fake": fake},
        policy={ModelRole.EXTRACTION: ModelChoice("fake", "extract-model")},
    )
    return EvidenceExtractionAgent(router), fake


def test_extracts_evidence_with_code_attached_provenance() -> None:
    src = _source()
    chunk = Chunk(source_id=src.id, text="The sky is blue.", position=0)
    out = _ExtractionOutput(claims=[_ExtractedClaim(claim="sky is blue", confidence=0.9)])
    agent, _ = _agent([out])
    evidence = asyncio.run(agent.extract([chunk], [src]))
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.claim == "sky is blue"
    assert ev.confidence == 0.9
    # Provenance comes from the Chunk/Source, NOT the model:
    assert ev.source_id == src.id
    assert ev.source_url == src.url
    assert ev.chunk_id == chunk.id
    assert ev.chunk_text == chunk.text
    assert ev.extracted_via == "extraction:extract-model"
    assert ev.id.startswith("ev_")


def test_uses_extraction_role_and_chunk_text_in_prompt() -> None:
    src = _source()
    chunk = Chunk(source_id=src.id, text="Fusion needs extreme pressure.", position=0)
    out = _ExtractionOutput(claims=[_ExtractedClaim(claim="needs pressure", confidence=0.8)])
    agent, fake = _agent([out])
    asyncio.run(agent.extract([chunk], [src]))
    assert fake.calls[0].model == "extract-model"
    assert "Fusion needs extreme pressure." in fake.calls[0].prompt


def test_per_chunk_empty_is_tolerated() -> None:
    src = _source()
    c1 = Chunk(source_id=src.id, text="nav boilerplate", position=0)
    c2 = Chunk(source_id=src.id, text="Real content here.", position=1)
    agent, _ = _agent(
        [
            _ExtractionOutput(claims=[]),  # c1 → no claims
            _ExtractionOutput(claims=[_ExtractedClaim(claim="real", confidence=0.7)]),
        ]
    )
    evidence = asyncio.run(agent.extract([c1, c2], [src]))
    assert [e.claim for e in evidence] == ["real"]


def test_zero_total_evidence_raises() -> None:
    src = _source()
    chunk = Chunk(source_id=src.id, text="boilerplate", position=0)
    agent, _ = _agent([_ExtractionOutput(claims=[])])
    with pytest.raises(ExtractionError):
        asyncio.run(agent.extract([chunk], [src]))


def test_unknown_source_raises() -> None:
    chunk = Chunk(source_id="src_does_not_exist", text="x", position=0)
    agent, _ = _agent([_ExtractionOutput(claims=[_ExtractedClaim(claim="x", confidence=0.5)])])
    with pytest.raises(ExtractionError):
        asyncio.run(agent.extract([chunk], []))
