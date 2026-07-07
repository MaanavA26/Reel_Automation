# ADR 0064: Uniform inter-sentence pause normalization via a `SegmentedTTSProvider` decorator

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

Issue #147 reports three distinct narration-quality defects from the first real
libass render: the voice (1) sounds artificial, (2) does not take uniform
breathing pauses between sentences like a human, and (3) pronounces the same
word ("octopus") differently at different points in the same render. Tracing
the seam confirmed the direct cause of (2): `KokoroTtsProvider._create_waveform`
(and every other `TTSProvider`) synthesizes the **entire** narration in one
`synthesize(text=...)` call — no chunking, no SSML/pause markup, no
sentence-boundary control. Whatever inter-sentence pacing exists today is
purely emergent from the model's behavior on one long, unsegmented block of
text; nothing in the pipeline enforces or normalizes it.

Issue #147 names three recommended fix directions and is explicit that they
are not equally in scope for one PR:

1. **Buildable now, deterministic, tool-layer:** insert explicit, uniform
   silence gaps between sentences via per-sentence synthesis + concatenation.
2. **Harder, may not be fully solvable with Kokoro alone:** pronunciation
   consistency (defect 3) — a phonemizer/inference characteristic, not a
   pacing problem.
3. **Owner decision:** TTS-provider quality tiering (paid/cloud for
   publish-quality, Kokoro for free bulk/dev) — a cost/quality tradeoff for the
   owner, not something to decide unilaterally in code.

**This ADR covers only (1).** Items (2) and (3) remain open, tracked on issue
#147.

## Decision

### A decorator `TTSProvider`, not a `MediaPipeline` change

`MediaPipeline.build` (`app/media/pipeline.py`) calls
``self._tts.synthesize(text=narration, voice=self._voice)`` **exactly once**
and gets back **exactly one** `SynthesizedSpeech` — this is the ADR 0025 timing
invariant (`track.cues[-1].end_ms == audio.duration_ms == video.duration_ms`)
and it must not change. So pause normalization is implemented as a new
`TTSProvider` — `SegmentedTTSProvider` (`app/media/tts/segmented.py`) — that
**wraps** any other `TTSProvider` and presents the identical
``synthesize(*, text, voice) -> SynthesizedSpeech`` signature. No caller
changes its call shape; the pipeline is unaware segmentation happens inside
the provider it was handed. This mirrors `TTSRouter`, the fabric's existing
provider-wrapping-provider precedent (a router wraps N providers for fallback;
this wraps one provider for post-processing).

Internally, `synthesize` does:

1. Split `text` into sentences (`split_into_sentences`).
2. Synthesize each sentence **sequentially** via the wrapped provider — not
   `asyncio.gather`. The default `KokoroTtsProvider` caches one ONNX model
   instance behind `asyncio.to_thread`; concurrent calls into it have
   undocumented thread-safety, so parallelizing is not worth the risk for this
   PR. Sequential synthesis is correct and simple; a future PR can revisit
   concurrency once a provider's thread-safety is verified.
3. Decode every resulting clip back to raw PCM (`_decode_wav_pcm16`).
4. Splice a fixed silence gap between every pair of clips — never before the
   first or after the last (`_splice_with_pauses`).
5. Re-encode the spliced samples via the **existing** `kokoro.encode_wav_pcm16`
   (reused, not reimplemented) and persist via this provider's own `sink`.
6. Return one `SynthesizedSpeech` whose `duration_ms` is computed **exactly**
   from the final sample count via the **existing**
   `kokoro.duration_ms_from_samples` (also reused, not hand-computed).

### Sentence splitting: a pragmatic regex, with a named limitation

`split_into_sentences` splits on `.`/`!`/`?` followed by whitespace
(`r"(?<=[.!?])\s+"`), strips each piece, and drops blanks — mirroring
`MediaPipeline._split_into_beats`'s non-blank-line filtering exactly (that
function already accepts a comparable simplification: it splits by line
without handling every edge case). **Known limitation, not solved here:**
abbreviations false-split — `"Dr. Smith arrived."` splits into `["Dr.", "Smith
arrived."]`. A real NLP sentence tokenizer (spaCy, nltk `punkt`, etc.) would
fix this at the cost of a new dependency; deferred as a follow-up, not blocking
this fix (an extra pause after "Dr." is a minor, rare artifact — nowhere near
as audible as the non-uniform pauses this ADR fixes). Whitespace-only or empty
`text` yields `[]` sentences, which `synthesize` treats as an error
(`SegmentedTtsError`) rather than silently synthesizing nothing.

### `pause_ms`: a named constant, a starting point, not a measured optimum

`DEFAULT_PAUSE_MS = 300` (milliseconds) is the constructor default. This is a
**reasonable starting point for a short human breath gap**, not a
scientifically derived or user-tested optimum. It is a named constant
specifically so it is trivial to tune once real renders are evaluated for
naturalness — the same "documented, not yet validated" posture the project
already uses for the DoD's numeric bands (`media.qc.rubric`) and the loudness
targets (`composition.loudness`). Tuning `pause_ms` per channel/voice is a
natural follow-up once there is a live render to listen to.

### WAV decode: the exact inverse of `encode_wav_pcm16`, no new dependency

`encode_wav_pcm16` (reused from `kokoro.py`) takes float samples in `[-1, 1]`
and maps each to a 16-bit int via `int(clamp(s, -1, 1) * 32767)`. Decoding
therefore divides each int16 frame by the same `32767.0` — the precise
inverse, so round-tripping through this seam introduces no *additional*
clipping or precision loss beyond the int16 quantization the source WAV
already carries. This was the single highest-risk implementation detail: an
earlier draft fed raw (unnormalized) int16 values straight into
`encode_wav_pcm16`, which — because that function clamps its input to
`[-1, 1]` before scaling — silently turned every non-zero speech sample into
full-scale noise while every *silent* (zero) sample stayed zero. That bug
would have passed a naive "durations add up" and even a naive "gaps are
silent" test, since both hold under it; it is caught here by an explicit test
asserting a known non-zero sample survives the decode/splice/re-encode round
trip at its real amplitude (`test_multi_sentence_splices_real_silence_at_exact_gap_positions`
in `tests/media/tts/test_segmented.py`) — verified by temporarily
reintroducing the bug during development and confirming exactly that
assertion (and only that one) fails.

Decoding uses stdlib `wave` + `array` only (no numpy), mirroring
`encode_wav_pcm16`'s own no-numpy discipline. It requires the wrapped
provider's clip to be **mono, 16-bit PCM WAV** — the shape every in-repo
adapter that calls `encode_wav_pcm16` emits (Kokoro today). A differently
shaped or non-WAV clip (e.g. a vendor's compressed response from
`HttpTtsProvider`) raises `SegmentedTtsError` rather than silently
misreading the samples — a known scope limit, not a defect: this PR's
target is the local Kokoro default the render that reported #147 used.

### Sample-rate agreement: required, never silently resampled

All per-sentence clips are expected to share one sample rate (one provider +
one voice produced all of them). A mismatch raises `SegmentedTtsError` naming
the disagreeing rates rather than silently resampling — resampling would be a
real signal-processing operation this PR has no reason to introduce.

### Degenerate case: single sentence returns the inner result verbatim

If `text` splits into exactly one sentence, `synthesize` returns the wrapped
provider's result **unchanged** — no decode/re-encode round trip, no pause,
byte-identical `audio_uri`/`duration_ms`/`produced_via` to calling the wrapped
provider directly. One consequence worth naming explicitly (not a bug):
`produced_via` therefore varies with sentence count — a single-sentence
narration keeps the wrapped provider's own value (e.g. `"tts:kokoro"`), while
a multi-sentence one reports `"tts:segmented+kokoro"`. A reviewer or an
automated check reading `produced_via` should be aware of this.

### Failure handling: propagate unwrapped

A per-sentence `synthesize()` failure is **never** caught inside
`SegmentedTTSProvider` — it propagates as the wrapped provider's own exception
type (e.g. `KokoroTtsError`), so the narration never silently loses content
and callers keep handling one error type per backend, exactly as today.
`SegmentedTtsError` is reserved for this seam's *own* contract failures (no
narratable sentence, sample-rate mismatch, an undecodable clip).

### A separate sink for the final artifact

The wrapped provider's `sink` persists its *per-sentence* clips — scratch,
intermediate artifacts of this process, not the final published audio.
`SegmentedTTSProvider` takes its **own** `AudioSink` (the same
`Callable[[bytes], str]` contract from `http_tts.py`) to persist the one final
spliced clip.

## Honesty notes (explicit, per project convention)

- **This PR does not address pronunciation consistency** (issue #147 defect
  3) — a phonemizer/inference characteristic of the Kokoro model on long
  single-call synthesis, unrelated to pausing. Per-sentence synthesis (this
  PR's mechanism) *may* incidentally help, since each sentence is now a
  shorter, independent inference call, but that is speculative and
  unverified — not a claimed fix.
- **This PR does not decide TTS-provider quality tiering** (issue #147's
  owner-decision item, the `multi-provider-model-fabric` broader tiering
  question). It stays exactly as configured today.
- **`pause_ms = 300` is unvalidated against a real render.** No live Kokoro
  synthesis or human listening test has confirmed 300ms sounds natural — it
  is a documented starting point (see "The design" above), consistent with
  the project's own posture on other DoD numeric bands (loudness targets,
  cut-rhythm thresholds) shipped ahead of live validation.
- **The splitter's boundary is punctuation, not the beat-join character —
  verify this interaction when wiring (#148).** `MediaPipeline.build` joins
  script beats with `"\n".join(segments)` before the single `synthesize`
  call. `split_into_sentences`'s boundary (`(?<=[.!?])\s+`) treats a `\n`
  between beats as a split point **only when the preceding beat already ends
  in `.`/`!`/`?`** — a deliberate choice (see "Sentence splitting" above), not
  an oversight, but it means an unpunctuated beat join collapses to the
  single-sentence fast path and silently inserts **no** pause there. Nothing
  in this PR observes real `script_outline` beats to know whether they
  reliably end in terminal punctuation, so the #148 wiring step should verify
  that assumption against real narration before relying on this fix in
  production.
- **Capability only — not yet wired into `MediaPipeline`'s construction**
  (`composition.py`). Enabling this in the actual render path is one
  constructor call away (`SegmentedTTSProvider(kokoro_provider, sink)` in
  place of the bare `kokoro_provider`), deferred to the separate P0
  spine-wiring effort tracked in issue #148 — the same "documented,
  not-yet-live" shipping pattern ADR 0053's generative-video adapters and ADR
  0062's `AeneasAligner` already use.
- **Requires WAV/PCM16 input from the wrapped provider.** `HttpTtsProvider`
  or a vendor adapter returning compressed audio (MP3/Opus/etc.) would fail
  loud with `SegmentedTtsError` rather than silently misdecoding — untested
  against any real non-Kokoro provider in this PR.
- **Sentence splitting is a regex, not an NLP tokenizer** — abbreviations
  false-split (see "Sentence splitting" above). Left as a known, documented
  edge case, exactly as `_split_into_beats` already accepts one.

## Consequences

**Positive.** Every render using `SegmentedTTSProvider` gets a uniform,
tunable, human-like breathing gap between sentences instead of whatever the
model's emergent pacing produces on one long call — directly addressing the
#147 defect this PR targets. Zero change to `MediaPipeline`'s call shape or
the ADR 0025 timing invariant (the pipeline still makes one `synthesize` call
and gets one `SynthesizedSpeech`; the pause math is entirely internal to the
new provider). No new dependency (stdlib `wave`/`array`/`re` only). Fully
provider-agnostic by construction — wraps Kokoro today, any future
WAV-emitting adapter tomorrow, with zero changes to this module.

**Negative / deferred.** Per-sentence synthesis means N inference calls
instead of 1 for an N-sentence narration — more total wall-clock time than one
long Kokoro call (unmeasured here; sequential by design, see "The design").
Pronunciation consistency and provider-quality tiering (issues #147's other
two items) remain fully open. `pause_ms` is unvalidated against a real
listening test. The sentence splitter's abbreviation limitation is accepted,
not solved.

**Risks.** Low and bounded. The splicing math is pure and unit-tested exactly
(sample counts, gap positions, and — critically — the decode round trip's
amplitude fidelity, the one place a silent correctness bug could hide). The
single-sentence fast path guarantees zero behavioral change for
already-short narrations. The seam is entirely opt-in (not wired into the
composition root in this PR), so it carries zero risk to the current render
path until a follow-up PR wires it in.

## Alternatives considered

- **SSML/pause markup passed to the TTS call.** Rejected: Kokoro's
  `kokoro.create(text, voice, speed, lang)` API has no documented SSML/pause
  markup support; even if it did, this would tie the fix to Kokoro's specific
  API rather than staying provider-neutral (CLAUDE.md §6).
- **Fixing this inside `KokoroTtsProvider` directly.** Rejected: it would
  couple pause-splicing logic to one specific adapter, requiring the same
  logic to be re-implemented in every other `TTSProvider` (HTTP, NVIDIA,
  HuggingFace, OpenAI). The decorator pattern (already proven by `TTSRouter`)
  gives every current and future provider the fix for free.
- **Changing `MediaPipeline` to call `synthesize` per-beat and concatenate
  itself.** Rejected: this is exactly the constraint the brief and ADR 0025
  rule out — the pipeline's one-call contract is load-bearing for the timing
  invariant, and pushing splicing logic into the pipeline would duplicate it
  for every future TTS-consuming caller instead of once, behind the provider
  seam.
- **A real NLP sentence tokenizer (spaCy/nltk) instead of the regex.**
  Rejected for this PR: a new dependency to fix a rare abbreviation edge case
  is disproportionate — the regex handles the overwhelming majority of
  narration text (which does not contain abbreviations like "Dr."), and the
  limitation is explicitly documented rather than silently accepted.
- **Resampling on a sample-rate mismatch instead of raising.** Rejected:
  resampling is a real signal-processing operation (with its own quality
  tradeoffs) this PR has no reason to introduce; a mismatch between
  same-provider, same-voice clips would indicate a real bug worth surfacing
  loudly, not papering over.
- **Concurrent per-sentence synthesis (`asyncio.gather`).** Rejected for this
  PR: `KokoroTtsProvider` caches one ONNX model instance behind
  `asyncio.to_thread`, and this repo has no evidence the underlying
  `kokoro_onnx` inference call is thread-safe under concurrent invocation.
  Sequential synthesis is correct and simple; revisit once a provider's
  thread-safety is verified (or when a provider is I/O-bound rather than
  CPU-bound, where concurrency is unambiguously safe).

## References

- Issue #147 (owner report: artificial pacing, inconsistent pronunciation,
  provider-quality tiering — recommendation 1 is this ADR's scope;
  recommendations 2 and 3 remain open)
- Issue #148 (P0 spine-wiring effort — the tracked follow-up for wiring this
  capability into the composition root)
- ADR 0019 (media provider seam abstraction), ADR 0022 (HTTP TTS adapter,
  the `AudioSink` contract), ADR 0025 (the `MediaPipeline` one-call timing
  invariant this ADR preserves), ADR 0046 (local Kokoro TTS — the provider
  this fix targets first), ADR 0049/0050 (`TTSRouter` — the
  provider-wrapping-provider precedent this ADR mirrors), ADR 0053
  (documented-not-yet-live adapter posture), ADR 0062 (`AeneasAligner`'s
  reuse of `resolve_local_path`, mirrored here)
