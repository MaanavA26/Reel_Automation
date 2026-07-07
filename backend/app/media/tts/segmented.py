"""A decorator ``TTSProvider`` that inserts uniform inter-sentence silence.

Addresses part of issue #147 (recommendation 1 only — the deterministic,
buildable-now fix): the owner reported the first real render's narration
"doesn't take uniform breathing pauses between sentences like a human". The
root cause is architectural, not a model defect — `KokoroTtsProvider` (and
every other `TTSProvider`) synthesizes the **entire** narration in a single
call, so any pacing between sentences is whatever the model emergently
produces on a long, unsegmented block of text; nothing in the pipeline
controls it.

Per CLAUDE.md §4 this is deterministic **tool** work (splicing audio with a
fixed silence gap) — no judgment, no model change. It is deliberately *not*
this PR's job to fix pronunciation consistency (issue #147's harder,
possibly-unsolvable-with-Kokoro-alone problem) or to pick a TTS-provider
quality tier (#147's owner-decision item); both stay open.

The load-bearing design constraint (mirrors `TTSRouter`, the fabric's other
provider-wrapping-provider precedent): `MediaPipeline.build` calls
``self._tts.synthesize(text=narration, voice=self._voice)`` exactly **once**
and gets back exactly one `SynthesizedSpeech` (ADR 0025's timing invariant
depends on this shape). So `SegmentedTTSProvider` is a `TTSProvider` itself —
same ``synthesize(*, text, voice) -> SynthesizedSpeech`` signature — wrapping
any other `TTSProvider` (Kokoro, HTTP, etc. — provider-agnostic). No caller
changes its call shape; the pipeline is unaware segmentation is happening
inside the provider it was handed.

Internally: split ``text`` into sentences, synthesize each one via the wrapped
provider (sequentially — the default `KokoroTtsProvider` caches one ONNX
model behind `asyncio.to_thread`, and concurrent calls into it are of unknown
thread-safety, so parallelizing is not worth the risk for this PR), decode
each resulting clip back to raw PCM, splice a fixed silence gap between every
pair of sentences (not before the first or after the last), re-encode via the
existing `encode_wav_pcm16`, persist via this provider's **own** injected
sink, and return one `SynthesizedSpeech` whose ``duration_ms`` is computed
exactly from the final sample count (`duration_ms_from_samples`).

Design decisions, made explicit rather than left implicit:

1. **Sentence splitting** (`split_into_sentences`) is a pragmatic regex — split
   on ``.``/``!``/``?`` followed by whitespace. **Known limitation:**
   abbreviations ("Dr. Smith arrived.") false-split, exactly like
   `MediaPipeline._split_into_beats` accepts an unhandled edge (blank-line-only
   splitting) rather than a full NLP sentence tokenizer. Not solved here.
2. **`pause_ms` default is `DEFAULT_PAUSE_MS = 300`** — a natural short breath
   gap chosen as a reasonable *starting point*, not a scientifically measured
   optimum. It is a named constant specifically so it is easy to tune later
   (e.g. once real renders are evaluated for naturalness).
3. **WAV decode/encode uses stdlib `wave` only** (no numpy), mirroring
   `kokoro.encode_wav_pcm16`. Decoding is `encode_wav_pcm16`'s precise inverse:
   each int16 frame is divided by ``32767.0`` (the same constant the encoder
   multiplies by) to recover the float amplitude, so splicing introduces no
   *additional* clipping or precision loss beyond the int16 quantization the
   wrapped provider's own WAV already has. This requires the wrapped
   provider's audio to be mono 16-bit PCM WAV (what every in-repo adapter that
   calls `encode_wav_pcm16` emits, e.g. Kokoro) — a non-WAV or differently
   shaped clip (e.g. a vendor's compressed MP3 response from `HttpTtsProvider`)
   raises `SegmentedTtsError` rather than silently mis-decoding. The
   decode/silence/splice primitives themselves now live in the shared
   `app.media.audio` module (ADR 0067 — the per-beat `NarrationSynthesizer`
   reuses the identical math); this provider normalizes that module's
   `AudioProcessingError` to its own `SegmentedTtsError`, so its public API
   and error contract are unchanged.
4. **Sample-rate agreement is required, never silently resampled.** All
   per-sentence clips must share one sample rate (expected, since one provider
   + one voice produced all of them); a mismatch raises `SegmentedTtsError`.
5. **Degenerate cases.** A single-sentence ``text`` returns the wrapped
   provider's result **verbatim** — no decode/re-encode round trip, no pause,
   byte-identical `audio_uri` to calling the wrapped provider directly. One
   consequence worth naming (not a bug): `produced_via` therefore varies by
   sentence count — single-sentence keeps the wrapped provider's own
   ``produced_via`` (e.g. ``"tts:kokoro"``); multi-sentence reports
   ``"tts:segmented+kokoro"``. Blank/whitespace-only sentences produced by the
   split are filtered before synthesis (mirroring `_split_into_beats`'s
   non-blank-line filtering); whitespace-only *input* yields zero sentences,
   which raises `SegmentedTtsError` rather than synthesizing nothing.
6. **Failure handling.** A per-sentence `synthesize()` failure is **never**
   caught here — it propagates as the wrapped provider's own exception type
   (e.g. `KokoroTtsError`), so the narration never silently loses content and
   callers keep handling one error type per backend, exactly as today.
7. **A separate sink.** The wrapped provider's ``sink`` persists its
   *per-sentence* clips — scratch/intermediate artifacts of this process, not
   the final published audio. `SegmentedTTSProvider` takes its **own**
   `AudioSink` (same `Callable[[bytes], str]` contract from `http_tts.py`) to
   persist the one final spliced clip.
"""

from __future__ import annotations

import re

# DEFAULT_PAUSE_MS moved to `app.media.audio` (its shared home, ADR 0067) and is
# re-exported here unchanged so existing importers of this module keep working.
from app.media.audio import (
    DEFAULT_PAUSE_MS,
    AudioProcessingError,
    read_wav_clip,
    splice_with_pauses,
)
from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import TTSProvider
from app.media.tts.http_tts import AudioSink
from app.media.tts.kokoro import duration_ms_from_samples, encode_wav_pcm16

PROVIDER_NAME = "segmented"

#: Splits on a sentence-ending mark followed by whitespace. A pragmatic
#: simplification (see the module docstring's Decision 1): it does not
#: special-case abbreviations, decimals, or quoted punctuation.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class SegmentedTtsError(RuntimeError):
    """Raised when segmentation-level splicing cannot be performed.

    Covers this seam's own failure modes — no narratable sentence, disagreeing
    per-sentence sample rates, or an undecodable per-sentence clip. Mirrors
    `KokoroTtsError` / `CompositionError`: one local error type for this
    provider's own contract failures. A per-sentence *synthesis* failure is
    **not** normalized to this type — it propagates as the wrapped provider
    raised it (see the module docstring, Decision 6).
    """


def split_into_sentences(text: str) -> list[str]:
    """Split ``text`` into non-blank sentences. Pure, deterministic.

    Splits on ``.``/``!``/``?`` followed by whitespace, then strips and drops
    any blank pieces the split can produce (e.g. trailing punctuation followed
    by trailing whitespace yields an empty final piece) — mirroring
    `MediaPipeline._split_into_beats`'s non-blank filtering. Whitespace-only or
    empty ``text`` yields ``[]``. See the module docstring's Decision 1 for the
    known abbreviation false-split limitation.
    """
    return [stripped for part in _SENTENCE_BOUNDARY.split(text) if (stripped := part.strip())]


class SegmentedTTSProvider:
    """A `TTSProvider` that inserts a uniform silence gap between sentences.

    Wraps another `TTSProvider` and presents the identical
    ``synthesize(*, text, voice) -> SynthesizedSpeech`` interface, so no caller
    (`MediaPipeline`, `composition.py`) needs to change its call shape. See the
    module docstring for the full design (splitting, sample-rate contract,
    single-sentence fast path, failure propagation, and the separate sink).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        inner: TTSProvider,
        sink: AudioSink,
        *,
        pause_ms: int = DEFAULT_PAUSE_MS,
    ) -> None:
        if pause_ms < 0:
            raise SegmentedTtsError(f"pause_ms must be non-negative, got {pause_ms}")
        self._inner = inner
        self._sink = sink
        self._pause_ms = pause_ms

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        """Synthesize ``text`` sentence-by-sentence and splice uniform pauses.

        Splits ``text`` into sentences (`split_into_sentences`); raises
        `SegmentedTtsError` if none remain (blank/whitespace-only input). A
        single sentence is passed straight through to the wrapped provider —
        no pause, no decode/re-encode round trip, byte-identical result to
        calling it directly. Two or more sentences are synthesized
        sequentially via the wrapped provider (any failure propagates
        unchanged — see Decision 6), decoded, spliced with the shared
        `app.media.audio.splice_with_pauses`, re-encoded, and persisted via
        this provider's own `sink`.
        """
        sentences = split_into_sentences(text)
        if not sentences:
            raise SegmentedTtsError(f"no narratable sentences found in text: {text!r}")

        if len(sentences) == 1:
            return await self._inner.synthesize(text=sentences[0], voice=voice)

        clips: list[SynthesizedSpeech] = []
        for sentence in sentences:
            clips.append(await self._inner.synthesize(text=sentence, voice=voice))

        decoded = [self._read_pcm_samples(clip.audio_uri) for clip in clips]
        try:
            samples, sample_rate = splice_with_pauses(decoded, self._pause_ms)
        except AudioProcessingError as exc:
            raise SegmentedTtsError(str(exc)) from exc

        audio_bytes = encode_wav_pcm16(samples, sample_rate)
        duration_ms = duration_ms_from_samples(len(samples), sample_rate)
        audio_uri = self._sink(audio_bytes)

        return SynthesizedSpeech(
            audio_uri=audio_uri,
            duration_ms=duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}+{self._inner.name}",
        )

    @staticmethod
    def _read_pcm_samples(audio_uri: str) -> tuple[list[float], int]:
        """Resolve + read a per-sentence clip's URI and decode it to PCM.

        Delegates to the shared `app.media.audio.read_wav_clip` (which reuses
        `resolve_local_path`, the same URI-resolution convention the ffmpeg
        composition adapter uses), normalizing its `AudioProcessingError` to
        this seam's own error type — the same pattern `AeneasAligner` uses
        for the identical reuse.
        """
        try:
            return read_wav_clip(audio_uri)
        except AudioProcessingError as exc:
            raise SegmentedTtsError(str(exc)) from exc
