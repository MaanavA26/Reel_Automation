"""Media pipeline — the Deep Research → Media handoff orchestrator (ADR 0025).

A deterministic *tool* (CLAUDE.md §3.3/§4 — no LLM, no judgment): given a Deep
Research `CreatorPacket` (the band-D handoff artifact, §5.4), it produces a
`MediaPlan` — an assembled-video descriptor — by chaining the three media seams:

    narrative selection → TTS synthesis → subtitle timing → composition

The judgment about *what* to narrate already happened upstream in the Short-Form
Content Strategist (the packet's `narratives`); this tool only *executes* the
deterministic assembly, mirroring `IngestionService`'s injected-provider DI and
its "tolerate per-item failures, raise only when nothing results" contract.

The intentional coupling point (ADR 0025 / ADR 0019 §4 exception)
-----------------------------------------------------------------
ADR 0019 §4 holds that ``app/media/`` imports nothing from ``app.schemas`` —
that keeps the seam modules (`tts`, `subtitles`, `composition`, `schemas`)
independently buildable. *This* module is the single, deliberate exception: it
is the cross-layer handoff seam ADR 0019 explicitly deferred, so it is the one
place the media layer is allowed to depend on the Deep Research schema
(`CreatorPacket`). Every other media module stays decoupled.

Timing invariant
-----------------
`CompositionService.render` takes a **single** `SynthesizedSpeech`, so narration
is synthesized **once** over the whole script and caption timings are allocated
across beats by **cumulative integer boundaries** (no per-segment rounding
drift). This guarantees, exactly:

    track.cues[-1].end_ms == audio.duration_ms == video.duration_ms
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from app.media.composition.base import CompositionService
from app.media.schemas import CaptionTrack, RenderedVideo, SynthesizedSpeech, _gen_id
from app.media.subtitles.base import DeterministicSubtitleService, SubtitleService
from app.media.tts.base import TTSProvider
from app.schemas.research_state import CreatorPacket, NarrativeOption

logger = logging.getLogger(__name__)


class MediaPipelineError(RuntimeError):
    """Raised when the packet yields no narratable script.

    Mirrors `IngestionService`'s `IngestionError`: per-beat failures (blank
    segments) are tolerated and skipped, but if the chosen narrative has no
    narratable beat at all — or the packet has no narrative to choose — the
    pipeline cannot produce a video and raises.
    """


class MediaPlan(BaseModel):
    """Assembled-video descriptor — the media tail of the research pipeline.

    The output of the creator-packet → media handoff: which narrative was
    chosen, the three produced artifacts (audio, captions, video), and a
    re-join key back to the source packet (``source_packet_id``, mirroring
    `CreatorPacket.report_id` / `KeyFact.finding_id`). Strict + id-prefixed
    like the rest of the layer's DTOs; ``produced_via`` records the orchestrator
    that assembled it, symmetric with the artifacts' own provenance.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _gen_id("plan"))
    source_packet_id: str
    narrative_title: str
    script_segments: list[str]
    audio: SynthesizedSpeech
    captions: CaptionTrack
    video: RenderedVideo
    produced_via: str


def _split_into_beats(script_outline: str) -> list[str]:
    """Split a narrative's ``script_outline`` into non-blank narration beats.

    Deterministic, line-oriented: one caption cue per non-blank line. Whitespace
    is stripped; blank lines are dropped (the per-beat "skip" of the contract).
    A line-based split keeps timing allocation exact and matches the beat-by-beat
    shape the strategist authors (`NarrativeOption.script_outline`).
    """
    return [stripped for line in script_outline.splitlines() if (stripped := line.strip())]


def _allocate_timings(segments: list[str], total_ms: int) -> list[tuple[int, int]]:
    """Allocate ``(start_ms, end_ms)`` per segment over ``total_ms``.

    Uses **cumulative** integer boundaries proportional to each segment's
    character length, so the last segment's ``end_ms`` equals ``total_ms``
    exactly — no rounding drift, no gaps, no overlaps. Empty-text guard: if the
    joined length is zero the time is split evenly (defensive; callers pass only
    non-blank segments, so this path is unreachable in practice).
    """
    lengths = [len(s) for s in segments]
    total_len = sum(lengths)
    timings: list[tuple[int, int]] = []
    prev_boundary = 0
    cumulative_len = 0
    for i, length in enumerate(lengths):
        cumulative_len += length
        if total_len > 0:
            boundary = round(total_ms * cumulative_len / total_len)
        else:
            boundary = round(total_ms * (i + 1) / len(lengths))
        timings.append((prev_boundary, boundary))
        prev_boundary = boundary
    return timings


class MediaPipeline:
    """Turns a `CreatorPacket` into a `MediaPlan` via the injected media seams.

    Constructor DI mirrors `IngestionService`: the `TTSProvider` and
    `CompositionService` are **required** (their real adapters are network/binary
    gated and deferred — ADR 0019), while ``subtitle_service`` **defaults to the
    real `DeterministicSubtitleService`** (it is pure, hermetic shipping code, so
    a default is safe — exactly as ingestion defaults ``pdf_parser`` to the real
    `PypdfParser`).

    ``visual_uris`` are accepted as a pass-through to composition: the packet
    carries no visuals and image/video sourcing is deferred (ADR 0019), so the
    pipeline neither invents nor requires a visual provider — an empty list
    renders narration + captions over a default background.
    """

    name = "pipeline"

    def __init__(
        self,
        tts_provider: TTSProvider,
        composition_service: CompositionService,
        subtitle_service: SubtitleService | None = None,
        *,
        voice: str = "narrator",
    ) -> None:
        self._tts = tts_provider
        self._composition = composition_service
        self._subtitles: SubtitleService = subtitle_service or DeterministicSubtitleService()
        self._voice = voice

    async def build(
        self,
        packet: CreatorPacket,
        *,
        narrative_index: int = 0,
        visual_uris: list[str] | None = None,
    ) -> MediaPlan:
        """Assemble a `MediaPlan` from the packet's chosen narrative.

        Picks ``packet.narratives[narrative_index]`` (deterministic selection —
        ranking would be judgment, which §4 bars from this tool), splits its
        ``script_outline`` into non-blank beats, synthesizes the joined narration
        once, allocates caption timings across the beats, builds the caption
        track, and composes. Raises `MediaPipelineError` if the packet has no
        narrative at that index or the chosen narrative has no narratable beat.
        """
        narrative = self._select_narrative(packet, narrative_index)
        segments = _split_into_beats(narrative.script_outline)
        if not segments:
            raise MediaPipelineError(
                f"narrative {narrative.title!r} produced no narratable script segments"
            )

        # Synthesize the whole narration once: the composition seam takes a
        # single audio artifact, so per-segment synthesis has nowhere to go.
        narration = "\n".join(segments)
        audio = await self._tts.synthesize(text=narration, voice=self._voice)

        timings = _allocate_timings(segments, audio.duration_ms)
        captions = self._subtitles.build_track(segments=segments, timings=timings)

        video = await self._composition.render(
            audio=audio,
            captions=captions,
            visual_uris=list(visual_uris or []),
        )

        return MediaPlan(
            source_packet_id=packet.id,
            narrative_title=narrative.title,
            script_segments=segments,
            audio=audio,
            captions=captions,
            video=video,
            produced_via=f"media:{self.name}",
        )

    @staticmethod
    def _select_narrative(packet: CreatorPacket, index: int) -> NarrativeOption:
        if not packet.narratives:
            raise MediaPipelineError(
                f"creator packet {packet.id} has no narrative options to render"
            )
        if not 0 <= index < len(packet.narratives):
            raise MediaPipelineError(
                f"narrative_index {index} out of range "
                f"(packet {packet.id} has {len(packet.narratives)} narratives)"
            )
        return packet.narratives[index]
