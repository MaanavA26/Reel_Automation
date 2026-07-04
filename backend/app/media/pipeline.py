"""Media pipeline — the Deep Research → Media handoff orchestrator (ADR 0025).

A deterministic *tool* (CLAUDE.md §3.3/§4 — no LLM, no judgment): given a Deep
Research `CreatorPacket` (the band-D handoff artifact, §5.4), it produces a
`MediaPlan` — an assembled-video descriptor — by chaining the three media seams:

    narrative selection → TTS synthesis → subtitle timing
        → (optional) word alignment → composition

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
is synthesized **once** over the whole script. Caption timings are allocated
across beats one of two ways (ADR 0065, issue #152):

* **No `word_aligner` configured, or alignment fails/can't be reconciled into
  full coverage:** `_allocate_timings`'s **cumulative, character-count
  proportional** boundaries (no per-segment rounding drift) — the original,
  still-supported path.
* **A `word_aligner` is configured and succeeds:** `_derive_timings_from_alignment`
  derives cue boundaries from the *same* per-word measurements attached to
  `Caption.words`, so a cue's declared `(start_ms, end_ms)` can never disagree
  with where its karaoke words actually land (the bug #152 traced back to two
  independent timing sources).

Either way this guarantees, exactly:

    track.cues[-1].end_ms == audio.duration_ms == video.duration_ms
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from app.media.alignment.base import AlignmentError, WordAligner
from app.media.composition.base import CompositionService
from app.media.schemas import (
    DEFAULT_CAPTION_STYLE,
    CaptionStyle,
    CaptionTrack,
    RenderedVideo,
    SynthesizedSpeech,
    WordSpan,
    _gen_id,
)
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


def _derive_timings_from_alignment(
    word_lists: list[list[WordSpan]], total_ms: int
) -> list[tuple[int, int]] | None:
    """Derive per-segment ``(start_ms, end_ms)`` boundaries from real alignment.

    ADR 0065's fix for issue #152: once a `WordAligner` has measured where each
    segment's words actually land in the audio, cue boundaries must come from
    *that same measurement* rather than `_allocate_timings`'s independent
    character-count guess — the two sources disagreeing (by up to +7.1s
    mid-video, per #152's measured numbers) is the root cause this function
    removes. Each segment's raw boundary is ``(first word's start_ms, last
    word's end_ms)``, adjusted by three rules so the result is a valid,
    fully-covering, non-overlapping track:

    1. **Gap bridging.** Unlike `_allocate_timings`, real alignment does not
       guarantee zero gaps between segments — e.g. `SegmentedTTSProvider`
       (#150) splices real ~300ms silences between sentences. Segment ``i``'s
       ``end_ms`` is extended forward to segment ``i + 1``'s real
       ``start_ms`` (``max(raw_end[i], raw_start[i + 1])``), so the caption
       track has **full coverage** (no caption-free dead air) instead of
       inventing a fake mid-utterance boundary — it just holds segment ``i``'s
       own (real) text a little longer, up to the moment segment ``i + 1``
       actually starts.
    2. **Endpoints are pinned, not trusted verbatim.** The first segment's
       ``start_ms`` is forced to ``0`` and the last segment's ``end_ms`` to
       ``total_ms`` exactly — mirroring `_allocate_timings`'s own guarantee.
       Real alignment may report a few ms of unassigned lead-in/trailing
       silence; pinning removes any dead, uncaptioned head/tail without
       touching any interior boundary.
    3. **All-or-nothing.** Returns ``None`` for the *entire* result — never a
       mix of derived and guessed boundaries — if full coverage can't be
       confidently derived: a segment with an empty word list (the aligner
       ran and the segment count matched, but produced zero words for one
       segment — a defensive check against an external tool's edge cases,
       not an expected case for non-blank text), an actual overlap between
       adjacent segments' aligned times (an alignment anomaly, not a silence
       gap — a real narrator cannot speak two segments at once), or any
       boundary landing outside ``[0, total_ms]``. Mixing derived boundaries
       for some cues with guessed ones for others would reintroduce a subtler
       version of the exact two-source-disagreement bug this function exists
       to remove. Callers must fall back to `_allocate_timings` for the whole
       narration on ``None``, and must not attach the word spans to any cue
       in that case either (a guessed boundary paired with real per-word
       timings is the same inconsistency, just relocated).

    Pure and hermetic: consumes already-produced `WordSpan` lists, does no I/O
    and never raises — every unrecoverable condition degrades to ``None``.
    """
    if not word_lists:
        return None
    for spans in word_lists:
        if not spans:
            return None

    n = len(word_lists)
    raw_starts = [spans[0].start_ms for spans in word_lists]
    raw_ends = [spans[-1].end_ms for spans in word_lists]

    timings: list[tuple[int, int]] = []
    for i in range(n):
        end = raw_ends[i] if i == n - 1 else max(raw_ends[i], raw_starts[i + 1])
        timings.append((raw_starts[i], end))

    # Pin the endpoints (rule 2) after the raw pass so pinning cannot itself
    # introduce an out-of-order boundary the loop below needs to catch.
    timings[0] = (0, timings[0][1])
    timings[-1] = (timings[-1][0], total_ms)

    prev_end = 0
    for start, end in timings:
        if start < prev_end or end < start or end > total_ms:
            return None
        prev_end = end
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

    ``word_aligner`` is **optional** (default ``None``): the word-timing source
    for karaoke captions is an external-tool-gated seam (ADR 0062), so the
    pipeline works exactly as before without one. When present, alignment is
    requested post-TTS **before** cue timings are decided (ADR 0065): if it
    succeeds and yields full coverage, cue boundaries are *derived from the
    same alignment* (`_derive_timings_from_alignment`) instead of guessed by
    `_allocate_timings`, and the per-word spans are attached to the caption
    cues. If alignment fails, miscounts, or can't be reconciled into full
    coverage, the pipeline falls back to `_allocate_timings` and every cue
    stays word-free — it never fails the render, and it never mixes a guessed
    boundary with real per-word timings.
    """

    name = "pipeline"

    def __init__(
        self,
        tts_provider: TTSProvider,
        composition_service: CompositionService,
        subtitle_service: SubtitleService | None = None,
        *,
        voice: str = "narrator",
        word_aligner: WordAligner | None = None,
    ) -> None:
        self._tts = tts_provider
        self._composition = composition_service
        self._subtitles: SubtitleService = subtitle_service or DeterministicSubtitleService()
        self._voice = voice
        self._word_aligner = word_aligner

    async def build(
        self,
        packet: CreatorPacket,
        *,
        narrative_index: int = 0,
        visual_uris: list[str] | None = None,
        segments: list[str] | None = None,
        caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE,
    ) -> MediaPlan:
        """Assemble a `MediaPlan` from the packet's chosen narrative.

        Picks ``packet.narratives[narrative_index]`` (deterministic selection —
        ranking would be judgment, which §4 bars from this tool) for its title and
        (absent an explicit ``segments`` override) its narration source.

        ``segments`` (ADR 0063) is the ordered list of narration/caption texts to
        render. When omitted (``None``, the default) the pipeline falls back to
        its original behavior: splitting the narrative's ``script_outline`` into
        non-blank lines via `_split_into_beats` — unchanged for any caller that
        does not pass beats explicitly. The caller (`VideoPipeline`) instead
        passes the full `ScriptBuilder`-produced HOOK→BUILD→PAYOFF→LOOP beat texts,
        so the pipeline narrates/captions the whole retention arc rather than only
        the narrative's own outline lines. Either way, the resulting segments are
        synthesized once; caption timings then come from real word alignment
        when a `word_aligner` is configured and succeeds, or from
        `_allocate_timings`'s character-count guess otherwise (ADR 0065); the
        caption track is built, and the video is composed with ``caption_style``
        (ADR 0059) passed through to `CompositionService.render`. Raises
        `MediaPipelineError` if the packet has no narrative at that index or the
        resulting segments are empty.
        """
        narrative = self._select_narrative(packet, narrative_index)
        if segments is None:
            segments = _split_into_beats(narrative.script_outline)
        if not segments:
            raise MediaPipelineError(
                f"narrative {narrative.title!r} produced no narratable script segments"
            )

        # Synthesize the whole narration once: the composition seam takes a
        # single audio artifact, so per-segment synthesis has nowhere to go.
        narration = "\n".join(segments)
        audio = await self._tts.synthesize(text=narration, voice=self._voice)

        # ADR 0065 (issue #152): when an aligner is configured, run it BEFORE
        # cue timings are decided, and derive the boundaries from the same
        # measurement the karaoke words come from — never from the
        # independent character-count guess. `word_lists` is only kept (and
        # only ever attached to cues below) once both alignment *and*
        # derivation succeed; any failure at either step falls back to
        # `_allocate_timings` for the whole narration with every cue
        # word-free, so a guessed boundary can never be paired with real
        # per-word timings.
        word_lists: list[list[WordSpan]] | None = None
        if self._word_aligner is not None:
            word_lists = await self._align_words(self._word_aligner, audio, segments)

        timings: list[tuple[int, int]] | None = None
        if word_lists is not None:
            timings = _derive_timings_from_alignment(word_lists, audio.duration_ms)
            if timings is None:
                logger.warning(
                    "word alignment succeeded but its spans could not be "
                    "reconciled into full-coverage cue boundaries (e.g. an "
                    "empty per-segment word list or an overlap); captions "
                    "degrade to cue-level fade (ADR 0065)",
                )
                word_lists = None  # never attach words without their own boundaries

        if timings is None:
            timings = _allocate_timings(segments, audio.duration_ms)

        captions = self._subtitles.build_track(segments=segments, timings=timings)
        if word_lists is not None:
            for cue, spans in zip(captions.cues, word_lists, strict=True):
                cue.words = list(spans)

        video = await self._composition.render(
            audio=audio,
            captions=captions,
            visual_uris=list(visual_uris or []),
            caption_style=caption_style,
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
    async def _align_words(
        aligner: WordAligner,
        audio: SynthesizedSpeech,
        segments: list[str],
    ) -> list[list[WordSpan]] | None:
        """Best-effort word alignment — degrades to ``None``, never fails the render.

        Requests per-segment word timings for the narration from the injected
        `WordAligner`. *Any* failure — a missing aeneas install, a non-zero
        exit, a malformed sync map, a per-segment count mismatch — is logged
        as a warning and returns ``None``, so `build()` falls back to
        `_allocate_timings` for cue boundaries and every cue stays word-free
        (ADR 0059 cue-level captions). The broad ``except Exception`` is
        deliberate: this is a provider-seam boundary (the TTS router's
        fallback uses the same posture) and karaoke is an enhancement, never
        worth failing a render over. Unlike the pre-ADR-0065 version of this
        method, it does **not** attach anything itself — `build()` owns
        attachment, because whether the returned spans may be attached now
        depends on whether `_derive_timings_from_alignment` can also turn them
        into valid cue boundaries (ADR 0065).
        """
        try:
            word_lists = await aligner.align(audio_path=audio.audio_uri, segments=segments)
            if len(word_lists) != len(segments):
                raise AlignmentError(
                    f"aligner returned {len(word_lists)} segment timing lists "
                    f"for {len(segments)} segments"
                )
        except Exception:
            logger.warning(
                "word alignment failed; captions degrade to cue-level fade (ADR 0059/0062)",
                exc_info=True,
            )
            return None
        return word_lists

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
