"""Per-beat narration synthesis with exact, construction-time cue offsets.

ADR 0067 (issue #159 — the root-cause fix direction for #146/#154): instead of
synthesizing the whole narration in one TTS call and then *estimating* where
each beat lands (character-count proportions pre-#153, alignment-derived
boundaries per ADR 0065), `NarrationSynthesizer` synthesizes **each narration
beat as its own clip**, splices the clips itself with a uniform silence gap,
and therefore *knows* every beat's ``(start_ms, end_ms)`` in the final audio
exactly — synthesis-time truth, no estimation step at all. Per CLAUDE.md §4
this is deterministic **tool** work (sequential synthesis + sample-exact
splicing; the upstream strategist decided *what* to say).

This is deliberately *not* a `TTSProvider`: its return type (`BeatNarration`)
carries more than a `SynthesizedSpeech` — the exact per-beat cue spans and the
per-clip URIs the per-clip word alignment (ADR 0067's second half) needs — so
pretending to be a provider would just force that data through a side channel.
`MediaPipeline` takes it as an explicit optional seam instead.

Design decisions, made explicit rather than left implicit:

1. **Splice math is shared, not duplicated.** Decode/silence/splice come from
   `app.media.audio` (extracted from `SegmentedTTSProvider`, #150/ADR 0064);
   re-encode/duration reuse `kokoro.encode_wav_pcm16` /
   `duration_ms_from_samples` exactly as `segmented.py` does. The gap length
   uses the same `silence_sample_count` rounding rule the splice itself uses,
   so the computed offsets can never disagree with where the gaps actually
   landed.
2. **Gap ownership: a cue's span includes its trailing gap.** Cue ``i`` ends
   exactly where cue ``i + 1`` starts (the inter-beat silence belongs to the
   *earlier* cue), and the last cue ends exactly at the total duration. This
   preserves ADR 0065's contiguity + full-coverage invariants (cues touch
   exactly, no caption-free dead air, ``cues[-1].end_ms == audio.duration_ms``)
   by construction — the same "hold the already-spoken text through the pause"
   posture ADR 0065's gap bridging chose deliberately.
3. **Offsets are computed in samples, then converted.** Boundaries are exact
   sample indices converted via `duration_ms_from_samples` (one rounding, at
   the end), so contiguity survives the ms conversion: cue ``i``'s end and cue
   ``i + 1``'s start are the *same* sample index, hence the same millisecond.
4. **Sequential synthesis** — same rationale as `SegmentedTTSProvider`
   (Decision: the default Kokoro backend's thread-safety under concurrent
   calls is unverified; ADR 0064).
5. **Failure handling: propagate, never drop content.** A per-beat
   `synthesize()` failure propagates as the wrapped provider's own exception
   type (never caught here), and a blank/empty segment list fails loud with
   `NarrationError` *before* any synthesis — narration must never silently
   lose a beat. `NarrationError` covers only this tool's own contract
   failures (no segments, a blank segment, an undecodable/mismatched clip).
6. **Single-beat fast path.** One segment returns the wrapped provider's
   clip **verbatim** as the final audio (no decode/re-encode round trip),
   with the one cue spanning ``(0, duration_ms)`` and the clip URI doubling
   as the final URI — mirroring `SegmentedTTSProvider`'s Decision 5,
   including the `produced_via` consequence (single-beat keeps the wrapped
   provider's own value; multi-beat reports ``"tts:per-beat+<inner>"``).
7. **A separate sink** persists the one final spliced WAV; the wrapped
   provider's own sink holds the per-beat clips — which are *not* scratch
   here: the per-clip aligner runs against them (ADR 0067), which is exactly
   why `BeatNarration` carries their URIs.
8. **WAV/PCM16-mono input required** from the wrapped provider (the shape
   every in-repo `encode_wav_pcm16` caller emits, e.g. Kokoro) — same known
   scope limit as `SegmentedTTSProvider`; a differently shaped clip raises
   `NarrationError` rather than silently mis-decoding.

Interaction with `SegmentedTTSProvider` (ADR 0064), verified against its
splice code: when the wrapped provider *is* a `SegmentedTTSProvider`, its
sentence-level pauses apply **inside** a beat only — its gaps go strictly
*between* sentences (never after the last one), so a beat ending in ``.``
carries no trailing intra-beat pause and the inter-beat gap comes solely from
this synthesizer. No double pause.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.media.audio import (
    DEFAULT_PAUSE_MS,
    AudioProcessingError,
    read_wav_clip,
    silence_sample_count,
    splice_with_pauses,
)
from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.http_tts import AudioSink
from app.media.tts.kokoro import duration_ms_from_samples, encode_wav_pcm16

SYNTHESIZER_NAME = "per-beat"


class NarrationError(RuntimeError):
    """Raised when per-beat narration cannot be synthesized/spliced.

    Covers this tool's own contract failures — no segments, a blank segment,
    a negative pause, or an undecodable/mismatched per-beat clip. Mirrors
    `SegmentedTtsError` / `KokoroTtsError`: one local error type. A per-beat
    *synthesis* failure is **not** normalized to this type — it propagates as
    the wrapped provider raised it (module docstring, Decision 5).
    """


@dataclass(frozen=True)
class BeatNarration:
    """The full result of per-beat narration synthesis (ADR 0067).

    ``speech`` is the one final, spliced narration artifact (the same shape
    the whole-narration TTS path produces); ``cue_timings`` are the **exact**
    per-beat ``(start_ms, end_ms)`` spans in that audio, known at construction
    (contiguous, gap-inclusive per Decision 2, first start ``0``, last end
    ``speech.duration_ms``); ``clip_uris`` are the per-beat clips' own URIs,
    parallel to the input segments — the audio the per-clip word aligner runs
    against (short tasks, so aeneas's cumulative long-audio DTW drift has
    nowhere to accumulate — #154's empirical 52ms → 1.6s finding).
    """

    speech: SynthesizedSpeech
    cue_timings: list[tuple[int, int]]
    clip_uris: list[str]


class NarrationSynthesizer:
    """Synthesizes narration beat-by-beat with exact cue offsets (ADR 0067).

    Constructor DI mirrors `SegmentedTTSProvider`: any `TTSProvider` to
    synthesize each beat, this tool's **own** `AudioSink` for the final
    spliced WAV, and a uniform inter-beat ``pause_ms`` (default the shared
    `DEFAULT_PAUSE_MS` — the same 300ms starting point ADR 0064 documents,
    still unvalidated by a live listening test).
    """

    name = SYNTHESIZER_NAME

    def __init__(
        self,
        tts_provider: TTSProvider,
        sink: AudioSink,
        *,
        pause_ms: int = DEFAULT_PAUSE_MS,
    ) -> None:
        if pause_ms < 0:
            raise NarrationError(f"pause_ms must be non-negative, got {pause_ms}")
        self._tts = tts_provider
        self._sink = sink
        self._pause_ms = pause_ms

    async def synthesize(self, *, segments: Sequence[str], voice: str) -> BeatNarration:
        """Synthesize each segment as its own clip and splice with uniform gaps.

        Fails loud (`NarrationError`) on an empty segment list or any blank
        segment *before* synthesizing anything — content is never silently
        dropped. Each segment is synthesized sequentially via the wrapped
        provider (its failures propagate unchanged, Decision 5); a single
        segment takes the verbatim fast path (Decision 6); two or more are
        decoded, spliced with `splice_with_pauses`, re-encoded, and persisted
        via this tool's own sink, with `cue_timings` computed from the exact
        sample layout (Decisions 2-3).
        """
        if not segments:
            raise NarrationError("no narration segments to synthesize")
        for i, segment in enumerate(segments):
            if not segment.strip():
                raise NarrationError(f"narration segment {i} is blank; refusing to drop content")

        clips: list[SynthesizedSpeech] = []
        for segment in segments:
            clips.append(await self._tts.synthesize(text=segment, voice=voice))

        if len(clips) == 1:
            clip = clips[0]
            return BeatNarration(
                speech=clip,
                cue_timings=[(0, clip.duration_ms)],
                clip_uris=[clip.audio_uri],
            )

        decoded = [self._read_clip(clip.audio_uri) for clip in clips]
        try:
            samples, sample_rate = splice_with_pauses(decoded, self._pause_ms)
        except AudioProcessingError as exc:
            raise NarrationError(str(exc)) from exc

        # Exact offsets from the splice's own layout: clip i starts after all
        # previous clips plus i gaps (in samples — the ms conversion happens
        # once per boundary, so touching boundaries stay identical, Decision 3).
        gap_samples = silence_sample_count(self._pause_ms, sample_rate)
        clip_starts_samples: list[int] = []
        cursor = 0
        for clip_samples, _ in decoded:
            clip_starts_samples.append(cursor)
            cursor += len(clip_samples) + gap_samples

        duration_ms = duration_ms_from_samples(len(samples), sample_rate)
        starts_ms = [duration_ms_from_samples(s, sample_rate) for s in clip_starts_samples]
        # Gap ownership (Decision 2): cue i ends where cue i+1 starts; the
        # last cue ends exactly at the total duration.
        cue_timings = [
            (start, starts_ms[i + 1] if i + 1 < len(starts_ms) else duration_ms)
            for i, start in enumerate(starts_ms)
        ]

        audio_bytes = encode_wav_pcm16(samples, sample_rate)
        audio_uri = self._sink(audio_bytes)
        speech = SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}+{self._tts.name}",
        )
        return BeatNarration(
            speech=speech,
            cue_timings=cue_timings,
            clip_uris=[clip.audio_uri for clip in clips],
        )

    @staticmethod
    def _read_clip(audio_uri: str) -> tuple[list[float], int]:
        """Read + decode a per-beat clip, normalized to this tool's error type."""
        try:
            return read_wav_clip(audio_uri)
        except AudioProcessingError as exc:
            raise NarrationError(str(exc)) from exc
