"""Hermetic tests for the structured per-run artifact sink (ADR 0057).

Fully hermetic: a populated `ResearchState` and a `MediaPlan` are built directly
from the schema models (no workflow run, no network, no ffmpeg) and persisted via
`FileRunArtifactSink` into a ``tmp_path`` standing in for the gitignored
``backend/runs/`` root. The tests cover the four required pillars: record shape,
structural round-trip, UTC ISO timestamps, and the gitignored-dir write layout.

The async ``write`` is driven via `asyncio.run` (the repo's no-pytest-asyncio
convention, mirroring `tests/channels/test_store.py`).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from app.media.pipeline import MediaPlan
from app.media.schemas import Caption, CaptionTrack, RenderedVideo, SynthesizedSpeech
from app.schemas.research_state import (
    CreatorPacket,
    Evidence,
    Finding,
    HookIdea,
    JobStatus,
    KnowledgeAcquisitionState,
    KnowledgeReasoningState,
    NarrativeOption,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchPublishingState,
    ResearchState,
    Source,
    SourceType,
    SubQuestion,
    SupportLevel,
    Synthesis,
    Verdict,
)
from app.services.runlog import FileRunArtifactSink, RunArtifactSink
from app.services.runlog.sink import (
    _ARTIFACT_KINDS,
    MEDIA_FILE,
    RUN_FILE,
    STATE_FILE,
)

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$")


def _state() -> ResearchState:
    """Build a fully populated, completed `ResearchState` across all bands."""
    src = Source(url="https://example.com/a", type=SourceType.WEB, discovered_via="search:fake")
    ev = Evidence(
        claim="water is wet",
        source_id=src.id,
        source_url=src.url,
        chunk_id="chk_1",
        chunk_text="water is wet because reasons",
        confidence=0.9,
        extracted_via="extract:fake",
    )
    verdict = Verdict(
        claim="water is wet",
        support_level=SupportLevel.SINGLE_SOURCE,
        supporting_evidence_ids=[ev.id],
        confidence=0.8,
        verified_via="verify:fake",
    )
    finding = Finding(
        statement="Water is wet.",
        detail="Per the single source.",
        sub_question_ids=["sq_1"],
        supporting_verdict_ids=[verdict.id],
        disputed=False,
        weakest_support=SupportLevel.SINGLE_SOURCE,
        synthesized_via="synth:fake",
    )
    report = Report(
        title="On Water",
        abstract="Water, examined.",
        sections=[
            ReportSection(
                heading="Wetness",
                narrative="Water is wet.",
                finding_ids=[finding.id],
                sub_question_ids=["sq_1"],
            )
        ],
        published_via="report:fake",
    )
    packet = CreatorPacket(
        report_id=report.id,
        hooks=[HookIdea(text="Did you know water is wet?", finding_ids=[finding.id])],
        narratives=[
            NarrativeOption(
                title="Wet Water",
                script_outline="Line one.\nLine two.",
                finding_ids=[finding.id],
            )
        ],
        published_via="packet:fake",
    )
    return ResearchState(
        topic="water",
        status=JobStatus.COMPLETED,
        plan=ResearchPlan(
            goal="understand water",
            sub_questions=[SubQuestion(id="sq_1", text="is water wet?")],
        ),
        acquisition=KnowledgeAcquisitionState(sources=[src], evidence=[ev]),
        reasoning=KnowledgeReasoningState(
            verdicts=[verdict],
            synthesis=Synthesis(findings=[finding]),
        ),
        publishing=ResearchPublishingState(reports=[report], packets=[packet]),
    )


def _media_plan(packet_id: str) -> MediaPlan:
    """Build a `MediaPlan` directly (no TTS/ffmpeg)."""
    return MediaPlan(
        source_packet_id=packet_id,
        narrative_title="Wet Water",
        script_segments=["Line one.", "Line two."],
        audio=SynthesizedSpeech(
            audio_uri="file:///tmp/a.wav",
            duration_ms=4000,
            voice="narrator",
            produced_via="tts:fake",
        ),
        captions=CaptionTrack(
            cues=[Caption(start_ms=0, end_ms=4000, text="Line one. Line two.")],
            produced_via="subtitles:deterministic",
        ),
        video=RenderedVideo(
            video_uri="file:///tmp/v.mp4",
            duration_ms=4000,
            width=1080,
            height=1920,
            produced_via="composition:fake",
        ),
        produced_via="media:pipeline",
    )


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_timestamps(value: object) -> list[str]:
    """Recursively collect every value under an ``*_at`` key (timestamp fields)."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if key.endswith("_at") and isinstance(sub, str):
                found.append(sub)
            found.extend(_iter_timestamps(sub))
    elif isinstance(value, list):
        for item in value:
            found.extend(_iter_timestamps(item))
    return found


def test_file_sink_satisfies_protocol(tmp_path: Path) -> None:
    sink = FileRunArtifactSink(tmp_path)
    assert isinstance(sink, RunArtifactSink)


def test_write_creates_run_dir_under_injected_base(tmp_path: Path) -> None:
    state = _state()
    sink = FileRunArtifactSink(tmp_path)

    asyncio.run(sink.write(state))

    run_dir = tmp_path / state.id
    assert run_dir.is_dir()
    # The run dir lives strictly under the injected (gitignored) base.
    assert run_dir.parent == tmp_path
    # Every artifact-kind file plus the run header and canonical state exist.
    assert (run_dir / RUN_FILE).is_file()
    assert (run_dir / STATE_FILE).is_file()
    for kind in _ARTIFACT_KINDS:
        assert (run_dir / f"{kind}.json").is_file(), kind


def test_record_shape_carries_run_id_and_flat_keys(tmp_path: Path) -> None:
    state = _state()
    sink = FileRunArtifactSink(tmp_path)
    asyncio.run(sink.write(state))
    run_dir = tmp_path / state.id

    run = _load(run_dir / RUN_FILE)
    assert isinstance(run, dict)
    assert run["run_id"] == state.id
    assert run["topic"] == "water"
    assert run["status"] == "completed"

    sources = _load(run_dir / "sources.json")
    assert isinstance(sources, list) and len(sources) == 1
    row = sources[0]
    assert row["run_id"] == state.id
    assert row["source_id"] == state.acquisition.sources[0].id
    assert row["type"] == "web"  # enum flattened to its string value

    findings = _load(run_dir / "findings.json")
    assert findings[0]["run_id"] == state.id
    assert findings[0]["weakest_support"] == "single_source"
    assert findings[0]["disputed"] is False

    # Narrative outline (the script source) is persisted in full.
    narratives = _load(run_dir / "narratives.json")
    assert narratives[0]["script_outline"] == "Line one.\nLine two."


def test_empty_bands_write_empty_arrays(tmp_path: Path) -> None:
    state = ResearchState(topic="empty")
    sink = FileRunArtifactSink(tmp_path)
    asyncio.run(sink.write(state))
    run_dir = tmp_path / state.id

    for kind in _ARTIFACT_KINDS:
        assert _load(run_dir / f"{kind}.json") == [], kind
    # No media plan supplied → no media file.
    assert not (run_dir / MEDIA_FILE).exists()


def test_media_record_written_when_plan_supplied(tmp_path: Path) -> None:
    state = _state()
    plan = _media_plan(state.publishing.packets[0].id)
    sink = FileRunArtifactSink(tmp_path)

    asyncio.run(sink.write(state, media_plan=plan))
    media = _load(tmp_path / state.id / MEDIA_FILE)

    assert isinstance(media, dict)
    assert media["run_id"] == state.id
    assert media["source_packet_id"] == state.publishing.packets[0].id
    assert media["script"] == "Line one.\nLine two."  # joined beats
    assert media["video_width"] == 1080
    assert media["video_uri"] == "file:///tmp/v.mp4"


def test_all_timestamps_are_iso_utc(tmp_path: Path) -> None:
    state = _state()
    plan = _media_plan(state.publishing.packets[0].id)
    sink = FileRunArtifactSink(tmp_path)
    asyncio.run(sink.write(state, media_plan=plan))
    run_dir = tmp_path / state.id

    stamps: list[str] = []
    for path in run_dir.glob("*.json"):
        stamps.extend(_iter_timestamps(_load(path)))

    assert stamps, "expected at least one timestamp across the records"
    for ts in stamps:
        assert _ISO_UTC.match(ts), ts
        # Parseable as an aware datetime, and the instant is UTC.
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utctimetuple() == parsed.astimezone(UTC).utctimetuple()


def test_state_round_trips_losslessly(tmp_path: Path) -> None:
    state = _state()
    sink = FileRunArtifactSink(tmp_path)
    asyncio.run(sink.write(state))

    raw = (tmp_path / state.id / STATE_FILE).read_text(encoding="utf-8")
    restored = ResearchState.model_validate_json(raw)

    # Structural round-trip: the canonical state re-hydrates equal to the original
    # (preserving aware-UTC timestamps and nested band substates).
    assert restored == state


def test_write_is_idempotent_per_run(tmp_path: Path) -> None:
    state = _state()
    sink = FileRunArtifactSink(tmp_path)

    asyncio.run(sink.write(state))
    first = (tmp_path / state.id / "sources.json").read_text(encoding="utf-8")
    asyncio.run(sink.write(state))
    second = (tmp_path / state.id / "sources.json").read_text(encoding="utf-8")

    # Re-writing the same state overwrites (no append/duplication) and is
    # byte-identical — the sink mints no write-time timestamps.
    assert first == second
    sources = _load(tmp_path / state.id / "sources.json")
    assert isinstance(sources, list) and len(sources) == 1
