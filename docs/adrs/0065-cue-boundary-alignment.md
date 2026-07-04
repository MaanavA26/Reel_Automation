# ADR 0065: Derive cue boundaries from real word alignment instead of a character-count guess

- **Status:** Accepted
- **Date:** 2026-07-04
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none (revises part of ADR 0062's "Alternatives considered")
- **Superseded by:** none

## Context

Issue #152 is a follow-up found via a real end-to-end live verification of #151
(merging #150+#151 locally and running the actual `MediaPipeline.build()` — not
a bypass script — with real Kokoro TTS, real `SegmentedTTSProvider` pause
normalization, a real forced aligner, and real ffmpeg-full/libass burn-in). It
measures the karaoke-highlight drift item #146 originally reported and confirms
it is **not** closed by #151 alone. On a real 5-cue, ~25s render, declared cue
boundaries diverged from the aligner's real word timings by up to **+7.1s**
mid-video (recovering only at the very last cue, because both timelines must
converge on the same total duration):

```
cue 0: declared=[0,4973]     real=[0,7320]      drift_end=+2347ms
cue 1: declared=[4973,10215] real=[7320,14000]  drift_end=+3785ms
cue 2: declared=[10215,15861] real=[14000,23000] drift_end=+7139ms
cue 3: declared=[15861,20968] real=[23000,24680] drift_end=+3712ms
cue 4: declared=[20968,24732] real=[24680,24720] drift_end=-12ms
```

**Root cause, precisely traced (#152):** `_format_karaoke_body`
(`app/media/subtitles/base.py`) clamps each word's offset into
`[0, cue.end_ms - cue.start_ms]` — correct, deliberate defensive behavior that
prevents a malformed `\k` tag from overflowing the cue's own ASS Dialogue
window. But `MediaPipeline.build` computed that window from
`_allocate_timings`'s character-count-proportional *guess*, then attached the
aligner's real per-word spans *inside* that guessed window. When the guess
under-estimates a cue's real speech duration, every word past the guessed
boundary clamps to the same collapsed tail position — several karaoke
highlights bunch up and snap together instead of sweeping, which is exactly
the user-reported "the highlight... often misses and lags behind the voice."
#151's aligner measurements were correct in isolation (157/157 words aligned
with sensible durations); the two timing sources — the guessed cue window and
the real per-word spans — were simply never reconciled.

This ADR is the scoped fix issue #152 names but defers: "re-order
`MediaPipeline.build()`'s TTS → allocate-timings → build-captions →
attach-word-timings sequence into synthesize → align (if available) →
derive-cue-timings-from-alignment (or fall back) → build-captions-with-correct-boundaries."

### This revises part of ADR 0062

ADR 0062 (word-level karaoke) explicitly considered and rejected this exact
fix, under "Alternatives considered":

> **Re-deriving cue boundaries from aligned words.** Rejected: it would break
> the ADR 0025 invariant (`cues[-1].end_ms == audio.duration_ms`) that the
> pipeline and its consumers lock; word spans stay an additive layer inside
> allocated cues, with formatter-side clamping absorbing seam overhang.

That objection is addressed directly here, not sidestepped: §Decision 3 below
(pinning the first cue's `start_ms` to `0` and the last cue's `end_ms` to
`audio.duration_ms` by construction, not by trusting the aligner's raw
endpoints) preserves the ADR 0025 invariant exactly, by construction, for
every successful derivation. ADR 0062's clamping in `_format_karaoke_body`
stays — it is still the last line of defense for any residual overhang — but
it is no longer doing the load-bearing work of hiding a systematically wrong
cue window; it now only absorbs genuine millisecond-level alignment noise.

## Decision

### 1. Reorder `MediaPipeline.build()`: align before deciding cue timings

When a `word_aligner` is configured, `build()` now calls
`self._align_words(...)` **immediately after** TTS synthesis and **before**
`_allocate_timings`/`build_track` run. `_align_words` (renamed from
`_attach_word_timings`) keeps its exact pre-existing degrade posture — a
missing tool, a non-zero exit, a malformed sync map, or a per-segment count
mismatch all log a warning and return `None` — but it no longer mutates cues
itself; it returns `list[list[WordSpan]] | None` so `build()` can decide
*both* the timings and the word attachment together, from one shared
success/failure outcome (see §3).

### 2. A new pure function: `_derive_timings_from_alignment`

```python
def _derive_timings_from_alignment(
    word_lists: list[list[WordSpan]], total_ms: int
) -> list[tuple[int, int]] | None
```

Each segment's raw boundary is `(first word's start_ms, last word's end_ms)`,
adjusted by two rules, with a third rule governing when to give up entirely:

- **Gap bridging (the chosen default).** `_allocate_timings` guarantees zero
  gaps (cumulative boundaries never leave dead air); real alignment does not
  — `SegmentedTTSProvider` (#150) splices real ~300ms silences between
  sentences, so segment *i*'s last word can end well before segment *i+1*'s
  first word starts. Segment *i*'s `end_ms` is extended forward to segment
  *i+1*'s real `start_ms` (`max(raw_end[i], raw_start[i+1])`). **Chosen over**
  leaving a caption-free gap during the silence: the Definition-of-Done rubric
  (ADR 0060) treats caption coverage as a hard band, and a bridged cue still
  shows only real, already-spoken text — it is held a little longer, not
  fabricated. Cues therefore always touch exactly (zero gap) or, if a genuine
  overlap is detected instead of a gap, the whole result is discarded (see the
  all-or-nothing rule below) — never left overlapping.
- **Pinned endpoints.** The first cue's `start_ms` is forced to `0` and the
  last cue's `end_ms` to `total_ms` exactly, regardless of the aligner's raw
  first/last timestamps. Real alignment can report a few milliseconds of
  unassigned lead-in or trailing silence; pinning removes any dead,
  uncaptioned head/tail and is exactly how this ADR discharges ADR 0062's
  invariant objection (§Context) — `cues[-1].end_ms == audio.duration_ms`
  holds by construction, not by hoping the aligner's last word lands exactly
  on the audio's true duration.
- **All-or-nothing.** The function returns `None` for the *entire* result —
  never a mix of derived and guessed boundaries — when full coverage can't be
  confidently derived:
  1. **Any segment has an empty word list.** Not an expected outcome for
     non-blank text, but the aligner is an external tool and this is a
     defensive contract check, exactly like the existing per-segment *count*
     check. Deliberately **not** partially derived (attaching real timings to
     the segments that did align while guessing the rest): that would
     reintroduce, per-cue, the exact two-independent-timing-sources
     inconsistency this whole ADR exists to remove — just relocated from
     "aligner vs. guess" to "some cues aligner, some cues guess."
  2. **An actual overlap** between adjacent segments' aligned times (segment
     *i*'s last word ends after segment *i+1*'s first word starts) — an
     alignment anomaly, not a silence gap, since a single narration can't
     speak two segments at once. Bridging would produce an overlapping cue
     pair, which is disallowed outright.
  3. **Any boundary lands outside `[0, total_ms]`** after the above — a
     residual defensive bound.

  Callers must fall back to `_allocate_timings` for the *whole* narration on
  `None`, and must not attach any word spans in that case either (§3).

The function is pure and takes no aligner, no audio, and does no I/O — every
case above is covered directly with fixture `WordSpan` lists.

### 3. `build()`'s fallback stays a simple `timings = derived or _allocate_timings(...)`

```python
word_lists = await self._align_words(...) if self._word_aligner is not None else None
timings = (
    _derive_timings_from_alignment(word_lists, audio.duration_ms)
    if word_lists is not None
    else None
)
if timings is None:
    word_lists = None  # never attach words without the boundaries they produced
    timings = _allocate_timings(segments, audio.duration_ms)
captions = self._subtitles.build_track(segments=segments, timings=timings)
if word_lists is not None:
    for cue, spans in zip(captions.cues, word_lists, strict=True):
        cue.words = list(spans)
```

`cue.words` is populated **only** when the exact same alignment result also
produced the cue's `(start_ms, end_ms)` — the one invariant this whole ADR
exists to establish: a cue's declared boundary and its karaoke word spans can
never come from two different sources. Both failure surfaces — `_align_words`
returning `None` (tool/count failure) and `_derive_timings_from_alignment`
returning `None` (coverage failure) — converge on the same fallback branch and
log a warning (distinct wording per surface, so a log reader can tell *which*
step degraded), matching the codebase's existing log-and-degrade posture at
provider-seam boundaries (the TTS router's fallback, `_align_words`'s own
broad `except Exception`).

## Honesty notes (explicit, per project convention)

- **Alignment accuracy itself is unchanged and still unvalidated on real
  hardware** (ADR 0062's own honesty section still applies) — this ADR fixes
  how a *given* alignment result is turned into cue boundaries, not whether
  the alignment itself is accurate. The #152 drift numbers came from a real
  aligner run; this fix has not yet been re-verified against a fresh live
  render (tracked as part of the CLAUDE.md §13 last-mile validation).
- **Gap bridging is a judgment call, not a measured optimum.** Extending a
  cue's end forward to the next cue's real start is the "full coverage over
  literal honesty" choice (§Decision 2); a channel that prefers a genuinely
  blank caption during real silence would need a different, currently
  unimplemented policy. Revisit if a live render's captions feel like they
  "hold" a phrase too long across a pause.
- **`_format_karaoke_body`'s clamp is intentionally untouched.** It remains
  correct defensive behavior for genuine millisecond-level alignment noise at
  a cue's own seam; this ADR's job was to stop feeding it *systematically*
  wrong boundaries, not to relax what it protects against.

## Consequences

**Positive.** A cue's declared timing and its karaoke word spans now provably
come from one source when an aligner is configured and succeeds — the class of
bug #152 measured (guessed boundaries disagreeing with real speech by up to
+7.1s) cannot recur through this path. The ADR 0025 timing invariant
(`cues[-1].end_ms == audio.duration_ms == video.duration_ms`) holds for both
the fallback and the new derived path. No public signature changes:
`MediaPipeline.build`'s signature, `_allocate_timings`, and the no-aligner
behavior are all byte-identical to before this PR.

**Negative / deferred.** The all-or-nothing fallback (point 1 above) means a
single aligner hiccup on one segment out of many discards real alignment for
the *entire* narration, not just that segment — a deliberate trade favoring
consistency over partial accuracy, but real cost if a given aligner run is
flaky on exactly one segment. Gap bridging's "hold the previous cue's text a
little longer" policy is unvalidated against a real listening/watching test.

**Risks.** Low and bounded. `_derive_timings_from_alignment` is pure and
covered by fixture-only unit tests for every branch (basic derivation, gap
bridging with and without drift across multiple gaps, pinned endpoints, empty
segment, real overlap, single segment, empty input); the reordering in
`build()` is covered by the direct regression test asserting cue boundaries
match aligned values rather than the pre-fix `_allocate_timings` guess (a test
that fails against the pre-fix code — see the test suite for the exact
before/after numbers); every existing degrade-path test (`aligner raises`,
`aligner miscounts`, `no aligner configured`) continues to pass unchanged.

## Alternatives considered

- **Leave `_format_karaoke_body`'s clamp as the only fix surface (loosen or
  rewrite the clamp).** Rejected: the brief and the root-cause trace are both
  explicit that the clamp is correct defensive behavior; the bug is upstream
  of it (feeding it a systematically wrong window), not in the clamp itself.
- **Leave a caption-free gap during real inter-sentence silence instead of
  bridging.** Considered as the "more honest" alternative (§Decision 2). Not
  chosen: it would make captions visibly disappear and reappear on every
  `SegmentedTTSProvider` pause, which reads as a rendering glitch rather than
  an intentional silence, and conflicts with the DoD rubric's caption-coverage
  expectation. Revisitable per-channel if a live check prefers it.
- **Partial derivation (mix derived boundaries for the segments that aligned
  cleanly with guessed boundaries for the rest).** Rejected outright — this is
  the literal shape of the bug this ADR removes, just distributed unevenly
  across cues instead of concentrated in the guess-vs-real mismatch. All
  failure modes converge on one full fallback (§Decision 2, point 3).
- **Keep `_attach_word_timings` mutating `captions.cues` directly instead of
  returning `word_lists`.** Rejected: the reordering means the pipeline must
  decide *whether* to attach words only after also knowing whether
  `_derive_timings_from_alignment` succeeded — a function that mutates cues
  immediately after aligning can't express "alignment succeeded but its
  boundaries didn't." Returning `list[list[WordSpan]] | None` and letting
  `build()` own attachment keeps both decisions in one place instead of
  reaching back into an already-mutated `CaptionTrack`.

## References

- Issue #152 (this ADR's fix; the measured drift numbers and root-cause trace)
- Issue #151 (real `WordAligner` wiring — necessary but not sufficient, per
  #152), issue #150 (`SegmentedTTSProvider`'s real inter-sentence silences —
  the concrete source of the gaps §Decision 2 bridges), issue #146 (the
  original bug report; item 2 of its body is this ADR's fix, now confirmed
  necessary — #146 itself stays open pending live re-verification)
- ADR 0062 (word-level karaoke forced alignment — the seam this ADR reorders
  around, and the "Alternatives considered" entry this ADR formally revisits),
  ADR 0025 (the `MediaPipeline` timing invariant this ADR preserves by
  construction rather than by trusting the aligner), ADR 0059 (ASS captions /
  `_format_karaoke_body`'s clamp, left untouched), ADR 0060 (the
  Definition-of-Done caption-coverage expectation §Decision 2's gap-bridging
  choice serves), ADR 0063 (wired the aligner into the production path this
  ADR's fix now benefits from)
