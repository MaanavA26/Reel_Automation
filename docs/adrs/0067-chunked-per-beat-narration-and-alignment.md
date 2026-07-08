# ADR 0067: Chunked per-beat narration synthesis + per-clip alignment — exact cue boundaries by construction

- **Status:** Accepted
- **Date:** 2026-07-08
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none formally — but demotes ADR 0065's boundary derivation
  (and therefore ADR 0066's boundary-side caveats) to the legacy path only
  when this ADR's opt-in seam is configured (see §Relationship to prior ADRs)
- **Superseded by:** none

## Context

Issues #146 (caption/karaoke sync) and #154 (aeneas crushing a segment to a
near-zero window) are the same root cause seen at two zoom levels. The repair
history so far:

- **#152/ADR 0065** made cue boundaries come from real alignment instead of a
  character-count guess — correct, but it made cue timing *hostage to
  alignment quality*.
- **#154/ADR 0066** then found alignment quality itself fails systematically:
  aeneas crushes the LAST segment of a long narration to ~40-52ms regardless
  of content (positional, proven by three isolating tests), and the guard
  could only clear the garbage karaoke words — **the 52ms cue window
  persisted**, because ADR 0066's re-review proved any neighbor-anchoring
  "correction" is a mathematical no-op on the derivation's contiguous,
  endpoint-pinned output.

**The decisive evidence (issue #154's comments, 2026-07-06/07):** the same
tail content that aligned to a 52ms window inside the full ~25s narration
got a sane **1.6-second window at a plausible 6.9 wps** when aligned as a
SHORT, isolated ~4.3s task. Cumulative DTW drift over long audio is the root
cause; short tasks give the drift nowhere to accumulate. #154 and the
2026-07-07 independent re-review both recommended chunked per-beat alignment
over any further guard/heuristic tuning, and issue #159 is the
owner-approved design this ADR records.

## Decision

### 1. The principle: synthesis-time truth beats any estimation

Every prior timing scheme *estimated* where beats land in audio the pipeline
received as an opaque whole (char-count proportions pre-#153; alignment-derived
boundaries per ADR 0065). This ADR removes the estimation problem instead of
improving the estimator: a new deterministic tool (CLAUDE.md §4),
`NarrationSynthesizer` (`app/media/narration.py`), synthesizes **each script
beat as its own clip** via the injected `TTSProvider`, splices the clips
itself with a uniform inter-beat silence gap, and persists one final WAV via
its own `AudioSink`. Because *it* performed the splice, every beat's
``(start_ms, end_ms)`` in the final audio is **known exactly at construction**
— returned as `BeatNarration` (the final `SynthesizedSpeech` + exact
`cue_timings` + the per-clip `clip_uris`).

Offsets are computed in **samples** and converted to ms once per boundary
(`duration_ms_from_samples`), using the *same* `silence_sample_count` rounding
rule the splice itself uses — so the offsets cannot disagree with where the
gaps actually landed, contiguity survives the ms conversion exactly, and the
last cue's end equals ``duration_ms`` by the same formula that produced
``duration_ms``.

### 2. Gap ownership: a cue's span includes its trailing gap

Cue ``i`` ends exactly where cue ``i + 1`` starts — the inter-beat silence
belongs to the **earlier** cue — and the last cue ends exactly at the total
duration. This preserves ADR 0065's invariants by construction: cues touch
exactly (no overlap), full coverage (no caption-free dead air during a
pause; the already-spoken text is held through it, ADR 0065's own
gap-bridging posture), and ``cues[-1].end_ms == audio.duration_ms``
(ADR 0025). A single-beat narration takes a verbatim fast path (the inner
clip *is* the final audio; one cue spans ``(0, duration_ms)``), mirroring
`SegmentedTTSProvider`'s Decision 5 including its `produced_via` consequence.

### 3. Shared audio primitives, not duplication

`SegmentedTTSProvider`'s decode/silence/splice privates (#150/ADR 0064) moved
verbatim to a new shared module, `app/media/audio.py` (`decode_wav_pcm16`,
`make_silence`/`silence_sample_count`, `splice_with_pauses`, `read_wav_clip`,
and the shared `DEFAULT_PAUSE_MS` constant, re-exported from `segmented.py`
unchanged). The module owns one error type (`AudioProcessingError`);
`segmented.py` and `narration.py` normalize it to their own seam errors, so
`SegmentedTTSProvider`'s public API, error contract, and byte-level behavior
are unchanged — its test suite passes unmodified. The WAV **encoder**
(`encode_wav_pcm16`) and `duration_ms_from_samples` stay in `kokoro.py`,
their original shared home (moving them would churn public imports and their
`KokoroTtsError` contract for zero behavioral gain).

### 4. Per-clip alignment in `MediaPipeline` — additive and opt-in

`MediaPipeline` gains an optional ``narration_synthesizer`` constructor
parameter (default ``None`` → the legacy whole-narration path, byte-identical,
all pre-existing tests pass unchanged). When configured:

- **Cue timings are the construction-time offsets, verbatim.**
  `_derive_timings_from_alignment` and `_allocate_timings` are never consulted
  on this path (they remain untouched for the legacy path).
- **The aligner (when present) runs once per clip** —
  ``align(audio_path=clip_uri, segments=[segment_i])``, the existing
  `WordAligner` contract with a one-element list; **no aligner API change** —
  and the returned clip-relative word times are shifted onto the narration
  clock by the clip's exact start offset. Short tasks are precisely the shape
  #154 validated as drift-free.
- **Failures are isolated per clip** (`_align_clip_words`): any failure on
  clip *i* logs a warning naming the segment and yields an empty word list —
  that one cue degrades to ADR 0059's plain cue-level fade; every other cue's
  words and every cue's timing are unaffected. This is deliberately *not*
  ADR 0065's all-or-nothing rule, and does not violate it: that rule exists to
  forbid mixing two *timing sources* across cues, and on this path there is
  only ever one timing source (construction) regardless of what alignment
  does. A word-free cue has no second source to disagree with.
- **The ADR 0066 plausibility guard runs unchanged** on the per-clip word
  lists (`_implausible_segment_indices`, the same function, word-data-only).
  One nuance follows from the single-timing-source fact: even if *every*
  clip's alignment is implausible, the response is still only "all cues
  word-free" — there is no boundary fallback to widen to, unlike the legacy
  path where all-implausible alignment also poisons the derived boundaries
  and rightly widens to `_allocate_timings`.
- **A per-beat *synthesis* failure propagates** (the `NarrationSynthesizer`
  contract, mirroring `SegmentedTTSProvider` Decision 6): narration never
  silently loses a beat.

### 5. Composition-root wiring: a new explicit flag

A new optional setting, ``narration_per_beat: bool = False``
(``REEL_AUTOMATION_NARRATION_PER_BEAT``, documented in `.env.example`),
mirrors ``aeneas_python_bin``'s additive opt-in pattern: when true,
`build_media_deps` constructs a `NarrationSynthesizer` over the same
supervised TTS provider and filesystem audio sink and carries it on
`MediaDeps`; `VideoPipeline` passes it through to `MediaPipeline`. **Chosen
over** inferring the path from "TTS + aligner both exist": the live rollout
should be explicit and reversible (flip one env var to A/B against the legacy
path on the same machine), and the per-beat path is independently useful
*without* an aligner — exact boundaries alone already fix the crushed-window
symptom, so coupling it to the aligner's presence would be wrong in both
directions.

## Relationship to prior ADRs

- **ADR 0065** (derive boundaries from alignment): untouched and still the
  legacy path's behavior; on the per-beat path its derivation is not needed —
  construction-time truth is strictly stronger than reconciling measured word
  spans. Its contiguity/coverage/endpoint invariants are preserved here by
  construction (§2).
- **ADR 0066** (plausibility guard): the detector is reused verbatim; its
  word-data-only principle carries over exactly. Its documented negative
  result (the persisting 52ms window) is what this ADR fixes at the root.
- **ADR 0064** (`SegmentedTTSProvider`): still fully compatible. When it
  wraps the inner provider, its sentence pauses apply **inside** a beat;
  inter-beat gaps now come from `NarrationSynthesizer`. **Double-pause
  interaction, verified against its splice code:** its gaps go strictly
  *between* sentences (`splice_with_pauses` inserts a gap only at
  ``index > 0``), never after a beat's last sentence — so a beat ending in
  ``.`` gets no trailing intra-beat pause and beat boundaries carry exactly
  one gap (this synthesizer's). No double pause. A side benefit: ADR 0064's
  honesty note warned that an *unpunctuated* beat join got **no** pause on
  the legacy ``"\n".join`` path; per-beat synthesis gives every beat boundary
  a uniform gap regardless of punctuation.
- **ADR 0025** (one `SynthesizedSpeech` into composition): preserved — the
  synthesizer returns exactly one final artifact; the invariant
  ``cues[-1].end_ms == audio.duration_ms == video.duration_ms`` holds on both
  paths.

## Honesty notes (explicit, per project convention)

- **Hermetic tests prove the math and the plumbing, NOT live alignment
  quality.** Everything here is verified with WAV-emitting fakes and scripted
  aligners; no aeneas ran. The claim that short per-clip tasks fix #146/#154
  rests on #154's one measured data point (52ms → 1.6s on the owner's
  machine) — **live verification on the owner's machine (real Kokoro + real
  aeneas + a real render) gates any claim of fixing #146/#154**, which both
  stay open until then. Per project lesson: merged + hermetic-green is not
  "the live path works."
- **N per-beat syntheses instead of 1** (sequential, same rationale as ADR
  0064) — more wall-clock per render, unmeasured. When the supervised TTS
  router is the inner provider, the TTS supervisor's judgment now runs per
  beat rather than once — more model calls, also unmeasured; acceptable under
  the quality-over-speed posture, but worth watching on the live run.
- **Costs: N aligner subprocesses instead of 1.** When a `WordAligner` is
  configured, the legacy path shells out to aeneas once for the whole
  narration; the per-beat path spawns one subprocess per beat. Each task is
  much shorter, so total alignment wall-clock stays comparable — but the
  per-process startup overhead multiplies by N, and that is real. Explicitly
  accepted for correctness: short, isolated tasks are the entire point (they
  deny aeneas's cumulative long-audio DTW drift anywhere to accumulate,
  #154's decisive measurement). The owner's live verification (PR #160,
  2026-07-08 — real Kokoro + real per-clip aeneas + real ffmpeg) ran the
  5-beat render through this path and it completed normally with every cue
  in the plausible speaking-rate band. Alignments run sequentially (same
  posture as the N syntheses); parallelizing via `asyncio.gather` is a
  possible later optimization, deliberately not taken here.
- **WAV/PCM16-mono input required** from the inner provider on the
  multi-beat path (same scope limit as ADR 0064): a compressed-audio vendor
  clip fails loud with `NarrationError`, untested against any real non-Kokoro
  provider.
- **`DEFAULT_PAUSE_MS = 300` between beats is a starting point** (ADR 0064's
  own caveat, inherited): an inter-*beat* gap arguably wants to be longer
  than an inter-*sentence* gap for dramatic pacing; tune after a live listen,
  the constant is one constructor argument away.
- **Word spans on this path are not clamped to their cue window here** — a
  per-clip alignment reporting words slightly past its clip's speech (into
  the gap the cue owns) renders fine; `_format_karaoke_body`'s existing
  formatter-side clamp (ADR 0059/0062) remains the defense for genuine seam
  overhang, unchanged.

## Consequences

**Positive.** Cue boundaries become exact by construction — the crushed-window
class of bug (#154's 52ms LOOP cue; #146's sync complaints to the extent they
stem from boundary estimation) cannot occur on this path even with **no
aligner at all**, a major win on its own. Alignment demotes from
timing-authority to pure karaoke enhancement, per-clip and individually
degradable. Uniform inter-beat pacing regardless of beat punctuation. Every
prior behavior is preserved byte-identically when the flag is off.

**Negative / deferred.** More synthesis calls per render (above). The legacy
path still carries ADR 0065/0066's known limitations when the flag is off.
`SegmentedTTSProvider` + `NarrationSynthesizer` together mean two decode/
re-encode generations of int16 quantization — inaudible in theory (each round
trip is exact within one quantization step), unmeasured in practice. The QC
gate still lacks a minimum-cue-duration check (#154's cheap-detector
suggestion) — untouched here, still worth adding as an independent safety
net.

**Risks.** Low and bounded. The synthesizer and audio primitives are pure
math over injected seams, covered by exact-arithmetic tests (offsets,
contiguity, gap ownership, zero-pause, splice silence/amplitude fidelity,
failure propagation); the pipeline's two paths are dispatched by one
``is not None`` check, with the legacy branch moved verbatim into
`_narrate_whole` and its entire pre-existing suite green unchanged.

## Alternatives considered

- **Keep whole-audio alignment and slice the audio per beat for realignment
  (a two-pass refine).** #154's own first sketch. Rejected: pass one's rough
  boundaries would still come from the drifting whole-audio alignment — the
  slice windows themselves inherit the error the second pass is meant to
  fix. Synthesizing per beat makes the boundaries exact *before* any aligner
  runs, and the clips for per-clip alignment already exist.
- **Implement per-beat synthesis as another `TTSProvider` decorator (the ADR
  0064 shape).** Rejected: the provider contract returns only a
  `SynthesizedSpeech`; the exact offsets and per-clip URIs — the entire point
  — would have to leak through a side channel. ADR 0064 itself rejected
  pipeline-side splicing *for a pause-only feature*; this feature is
  precisely about the pipeline knowing the offsets, so an explicit richer
  seam (`BeatNarration`) is the honest shape.
- **Auto-enable when TTS + aligner are both configured (no new flag).**
  Rejected (§5): silently changes live behavior on existing configurations,
  couples the path to the aligner it does not require, and removes the
  explicit A/B lever the last-mile validation wants.
- **WhisperX (neural aligner) instead.** Already deprioritized in #154 by the
  owner: chunking keeps the lightweight CPU aligner (the 8GB-laptop
  constraint, issue #136), has no new hardware tradeoff, and is the approach
  with direct empirical validation. A WhisperX adapter behind the same
  `WordAligner` seam remains the tracked accuracy follow-up (ADR 0062).
- **Tune the ADR 0066 guard further / add boundary heuristics.** Rejected by
  the evidence trail: the 2026-07-07 re-review proved the last heuristic a
  no-op, and both it and #154 explicitly recommended removing the root cause
  over detecting more symptoms.

## References

- Issue #159 (the owner-approved design this ADR records), issue #154 (the
  root-cause diagnosis, the three isolating tests, and the decisive
  short-task measurement: the crushed tail's 52ms window → **1.6s at 6.9
  wps** when aligned as an isolated ~4.3s task; **stays open pending live
  verification**), issue #146 (the original caption-sync report; **stays
  open** likewise)
- PR #155's independent re-review verdict (2026-07-07) — the no-op proof
  that closed the guard-tuning direction
- ADR 0064 (`SegmentedTTSProvider` — the splice primitives' origin, the
  intra-beat pause layer, and the double-pause interaction verified in
  §Relationship), ADR 0065 (boundary derivation — now legacy-path-only when
  this seam is on; its invariants preserved by construction), ADR 0066 (the
  plausibility guard — reused verbatim, word-data-only), ADR 0025 (the
  single-artifact composition contract, preserved), ADR 0059/0062 (cue-level
  fade degrade target; the `WordAligner` seam and formatter clamp), ADR 0063
  (the wiring pattern `narration_per_beat` mirrors)
