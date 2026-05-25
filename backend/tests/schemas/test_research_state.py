"""Tests for the Deep Research state and provenance schema (ADR 0001)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.research_state import (
    Chunk,
    Evidence,
    JobStatus,
    KnowledgeAcquisitionState,
    ResearchState,
    Source,
    SourceType,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "research_state_minimal.json"


def test_research_state_defaults() -> None:
    state = ResearchState(topic="Quantum supremacy")

    assert state.topic == "Quantum supremacy"
    assert state.status == JobStatus.QUEUED
    assert state.id.startswith("job_")
    assert isinstance(state.acquisition, KnowledgeAcquisitionState)
    assert state.acquisition.sources == []
    assert state.acquisition.chunks == []
    assert state.acquisition.evidence == []


def test_id_format_prefixes() -> None:
    state = ResearchState(topic="t")
    source = Source(url="https://x", type=SourceType.WEB)
    chunk = Chunk(source_id=source.id, text="x")
    evidence = Evidence(
        claim="x",
        source_id=source.id,
        source_url=source.url,
        chunk_id=chunk.id,
        chunk_text="x",
        confidence=0.5,
        extracted_via="ext_v1",
    )

    assert state.id.startswith("job_")
    assert source.id.startswith("src_")
    assert chunk.id.startswith("chk_")
    assert evidence.id.startswith("ev_")


def test_confidence_out_of_range_rejected() -> None:
    base: dict[str, object] = {
        "claim": "x",
        "source_id": "src_a",
        "source_url": "https://x",
        "chunk_id": "chk_a",
        "chunk_text": "x",
        "extracted_via": "ext_v1",
    }
    with pytest.raises(ValidationError):
        Evidence(**base, confidence=1.1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Evidence(**base, confidence=-0.1)  # type: ignore[arg-type]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ResearchState.model_validate({"topic": "x", "spurious_field": "oops"})


def test_timestamps_are_timezone_aware() -> None:
    state = ResearchState(topic="t")
    assert state.created_at.tzinfo is not None
    assert state.updated_at.tzinfo is not None


def test_round_trip_json() -> None:
    state = _build_minimal_populated_state()

    blob = state.model_dump_json()
    parsed = ResearchState.model_validate_json(blob)

    assert parsed == state


def test_json_shape_matches_committed_fixture() -> None:
    """Canary against accidental schema-shape changes.

    Update the committed fixture deliberately when the schema legitimately
    evolves; that update is itself a reviewable signal of a backward-compat
    change.
    """
    fixture = json.loads(FIXTURE_PATH.read_text())

    state = ResearchState.model_validate(fixture)

    # Field equality against a programmatically-built reference state.
    reference = _build_minimal_populated_state()
    assert state == reference


def _build_minimal_populated_state() -> ResearchState:
    source = Source(
        id="src_fixed_001",
        url="https://example.com/paper",
        type=SourceType.PAPER,
        title="Example paper",
        discovered_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    chunk = Chunk(
        id="chk_fixed_001",
        source_id=source.id,
        text="The thing happened.",
        position=1,
    )
    evidence = Evidence(
        id="ev_fixed_001",
        claim="The thing happened.",
        source_id=source.id,
        source_url=source.url,
        chunk_id=chunk.id,
        chunk_text=chunk.text,
        confidence=0.9,
        extracted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        extracted_via="extraction_v1",
    )
    return ResearchState(
        id="job_fixed_001",
        topic="Example research topic",
        status=JobStatus.RUNNING,
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        acquisition=KnowledgeAcquisitionState(
            sources=[source],
            chunks=[chunk],
            evidence=[evidence],
        ),
    )
