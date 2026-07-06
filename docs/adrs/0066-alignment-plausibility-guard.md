# ADR 0066: Per-segment plausibility guard against implausible word alignment

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none (adds a deliberate, narrow exception to part of ADR
  0065's "all-or-nothing" decision — see §Relationship to ADR 0065 below)
- **Superseded by:** none

## Context

Issue #154 was found via a real end-to-end live verification of #153 (ADR
0065's cue-boundary-derivation fix for #152/#146): #153's fix was confirmed
working correctly — declared cue boundaries now exactly match real alignment
(e.g. cue 0 correctly reads `[0, 7320]`, matching the aligner's actual
measurement, versus the old character-count guess of `[0, 4973]`). **But the
same re-verification surfaced a new, more fundamental problem underneath the
now-correctly-wired alignment.**

### What was found

The LAST segment in a multi-segment narration consistently gets crushed to a
near-zero aligned duration by aeneas, regardless of its actual text content.
A real 5-beat render (hook/build/build/payoff/loop) measured:

```
segment 0 (hook,   11 words): [0-7320]      -- normal pace
segment 1 (build1, 14 words): [7320-14000]  -- normal pace
segment 2 (build2, 18 words): [14000-23000] -- normal pace
segment 3 (payoff, 11 words): [23000-24680] -- fast (6.5 wps) but plausible
segment 4 (loop,   11 words): [24680-24732] -- 52ms for 11 words. Impossible.
```

Three isolating tests confirmed the failure is **positional, not
content-specific**, and not an interaction with prior work:

1. **Ruled out #150/#151/#153 interaction.** Reproduced the identical crushing
   pattern using plain, unsegmented Kokoro audio (no `SegmentedTTSProvider`
   pause insertion at all) — same 5 segments, same pathology (last segment
   crushed to 40ms). Not caused by pause normalization.
2. **Ruled out content-specificity, confirmed position-specificity.** Swapped
   the beat order so the previously-fine segment became last and the
   previously-crushed segment moved to 4th position. Result: the new *last*
   segment gets crushed and the moved segment aligns normally. The bug
   follows *position*, not text content.
3. **Ruled out a simple trailing-silence fix.** Padded the audio with 1s of
   trailing silence via ffmpeg's `apad` (a known DTW tail-boundary
   mitigation) — the crushing persisted, just shifted slightly.

**Likely root cause (flagged, not confirmed):** the reference machine's aeneas
install runs in pure-Python fallback mode (`AENEAS_WITH_CEW=False`, no C
extensions — "Unable to load Python C Extensions, Running the slower pure
Python code" printed on every run). DTW-based forced alignment is known to
have boundary-condition artifacts at sequence edges; aeneas's own docs flag
the C extensions as more numerically robust. This ADR does **not** fix the
root cause (enabling aeneas's C extensions, or a future higher-accuracy
`WordAligner` such as a WhisperX adapter behind the same seam, are the
tracked accuracy follow-ups) — it fixes the pipeline's response to whatever
an aligner reports, exactly as ADR 0065 fixed how a *given* alignment result
becomes cue boundaries without validating the alignment's accuracy itself.

### Severity

Every real video's last beat is the LOOP (the deliberate retention/re-hook
callback, ADR 0061) — the systematic tail-crushing means the loop's
captions/karaoke are unusable (a near-instant flash) on every single real
render, not an edge case. ADR 0065's fix is architecturally correct (a cue's
boundary and its karaoke words now provably come from one source) but
faithfully inherits whatever the alignment source reports: garbage in,
garbage out.

## Relationship to ADR 0065

ADR 0065 established an explicit **all-or-nothing** principle for
`_derive_timings_from_alignment`: never partially mix derived and guessed
boundaries across different cues, because doing so "would reintroduce a
subtler version of the exact two-source-disagreement bug this function
exists to remove." That principle is correct and stays **unchanged** for the
failure category it was designed for: **total alignment failure** — an
exception raised by the aligner, a per-segment count mismatch, or a segment
with a genuinely empty word list. All three still route to the whole
narration's `_allocate_timings` fallback with every cue word-free, exactly as
ADR 0065 built it. This ADR does not touch that path.

#154 is a **different failure category**: alignment does not fail — it
*succeeds*, for every segment, with a full per-segment word list and a
result `_derive_timings_from_alignment` accepts as valid (no empty list, no
overlap, every boundary in range). The data is present; it is simply *wrong*
for one or more specific segments. Reusing ADR 0065's whole-narration
fallback for this category would be a category error, not just "the simpler
choice": #154's own diagnosis proved the failure is **positional and
deterministic** — the last segment of a multi-segment narration is reliably
crushed, and every real render's last segment is the LOOP beat (ADR 0061).
A plausibility check that triggered the *whole-narration* fallback would fire
on **nearly every real multi-beat render**, since there is almost always a
last segment — permanently disabling real cue-boundary derivation for every
video and completely defeating #151/#152/#153's purpose (real, per-word
alignment reaching production at all). The correct response has to be
proportionate to the failure: a bad segment's own data is discarded, and
only that segment's boundary and words are recomputed; every other segment
that aligned correctly keeps its real, aligned boundary and words exactly as
ADR 0065 already produces them.

This is therefore a deliberate, reasoned, narrow exception to ADR 0065's
all-or-nothing stance — reserved for "the aligner's own per-segment output is
individually implausible" and never applied to "the aligner failed
outright." The two failure categories keep their own, appropriately-scoped
responses; neither is weakened by the other's existence.

## Decision

### 1. A plausibility check on already-derived, already-aligned segments

`MediaPipeline.build` already calls `_align_words` then
`_derive_timings_from_alignment` (ADR 0065, unchanged). When that succeeds
(returns a non-`None` `timings` list), a new pure function runs:

```python
def _implausible_segment_indices(
    word_lists: list[list[WordSpan]],
    *,
    max_words_per_second: float = MAX_PLAUSIBLE_WORDS_PER_SECOND,
) -> set[int]
```

For each segment with a **non-empty** word list, it computes the implied
speaking rate — `word_count / ((last_word.end_ms - first_word.start_ms) /
1000.0)` — and flags the segment's index if that rate exceeds the threshold.
A zero-or-negative-duration span (an infinite implied rate) is automatically
flagged rather than raising `ZeroDivisionError`; #154's own reproductions
include exactly this shape (a duration crushed small enough to be
effectively zero).

**A segment with an empty word list is deliberately skipped, not flagged.**
That is `_derive_timings_from_alignment`'s existing total-failure trigger — a
different failure category (the aligner produced *nothing* for a segment)
from this function's target (the aligner produced words, but their timing is
nonsense). Conflating the two would double-count one failure as two and,
worse, would let this function's per-segment salvage silently absorb a case
ADR 0065 deliberately treats as total failure.

### 2. The threshold: `MAX_PLAUSIBLE_WORDS_PER_SECOND = 8.0`

Sustained human speech — including fast TTS narration — does not exceed
roughly 4-5 words/second. `8.0` is a deliberately generous margin (roughly
double that ceiling) chosen so genuinely fast-but-real speech never
false-triggers this guard; #154's own "payoff" segment measured 6.5 wps and
is explicitly called out in the issue as "fast but plausible" — comfortably
under 8. #154's actual crushed segments imply 200+ words/second (11 words in
40-52ms), more than an order of magnitude past the threshold, so the margin
costs nothing in practice: there is no plausible real-speech scenario this
close to the line, only the confirmed pathological one far past it. The
check is a strict `>` (a rate *equal* to the threshold is not flagged) —
tested directly at 8.0 wps (not flagged) and 8.008 wps (flagged).

### 3. The fallback: anchor to the nearest plausible neighbors

A new pure function replaces exactly the flagged segments' boundaries:

```python
def _anchor_implausible_segments(
    timings: list[tuple[int, int]],
    implausible: set[int],
    total_ms: int,
) -> list[tuple[int, int]]
```

For each implausible index `i` (processed in increasing order):

- `start_ms` = the previous cue's already-finalized real `end_ms` (`0` if
  `i` is the first segment).
- `end_ms` = the next **plausible** segment's real, untouched `start_ms`
  (`total_ms` if `i` is the last segment, or every later segment is also
  implausible).

This fills exactly the gap between the segment's nearest trustworthy
neighbors — the same "fill the real gap, don't fabricate" philosophy ADR
0065's own gap bridging already uses (its Decision 2), applied to a "this
neighbor's data is untrustworthy" gap instead of a "real inter-sentence
silence" gap. Every entry **not** in `implausible` is returned
byte-identical to `timings`'s own value — the whole point, since those
segments' real alignment is trustworthy and must not be perturbed.

Processing indices in increasing order means a **consecutive run** of two or
more implausible segments still anchors correctly: each one's start is the
previous one's just-computed end, keeping the run contiguous. A documented
consequence: within such a run, the first segment absorbs the entire gap up
to the next plausible segment's start, and every later segment in the same
run collapses to zero width (they all resolve to the same "next plausible
segment," hence the same end boundary). This never violates the ADR 0025
ordering/coverage invariants — cues stay strictly ordered, touching, and
non-overlapping — it is simply an even split this function does not attempt,
since #154's own evidence is a single crushed segment (never a run), and a
multi-segment failure is a pathological case this function only needs to
*not break* on, not optimize for.

**A mathematical honesty note.** For the common shape #154 actually
diagnosed — an implausible segment whose own reported *start* remains
trustworthy (inherited correctly from where the previous segment's real
speech ends) and only its internal *duration/end* collapses — this anchor
computation is provably equivalent to what ADR 0065's own bridging
(`max(raw_end[i], raw_start[i+1])`) and pinning already produce for that
segment's boundary. The always-present, guaranteed improvement in that case
is the words (see §4 below); the boundary computation's real, visible
divergence from naive re-use of ADR 0065's own derivation shows up
specifically for a **consecutive run** of implausible segments, where naive
bridging would otherwise chain-trust one implausible segment's own (also
bad) reported start to compute its plausible predecessor's end. This
function never does that: it always skips past every implausible index to
find the next segment it actually trusts.

### 4. Words are always cleared for a flagged segment

`cue.words` is set to `[]` for every index in `implausible`, never the
crushed span — a cue with no words degrades to the existing plain cue-level
fade (ADR 0059), exactly like a total-alignment-failure cue does today. This
is the fix's always-present, unconditional improvement: even in the (common)
case where the anchor boundary coincides with what ADR 0065's own derivation
already computed (§3's honesty note), the previously-attached garbage word
spans — the direct cause of #154's "near-instant flash" symptom — are gone.

### 5. Pathological edge case: every segment implausible

If `len(implausible) == len(word_lists)` — no evidence this occurs in
practice, per #154's own observations, which show a single crushed segment
in every case — there is no trustworthy neighbor anywhere to anchor to. This
widens to the same whole-narration `_allocate_timings` fallback ADR 0065
already uses for total failure, rather than producing a degenerate
all-zero-width result. `build()` logs a distinct message for this case so a
log reader can tell it apart from both the per-segment fallback and ADR
0065's own total-failure message.

## Consequences

**Positive.** The systematic, positional bug #154 diagnosed — every real
render's last beat (the LOOP, ADR 0061) rendering an unusable karaoke flash —
cannot recur through this path while leaving every other, correctly-aligned
segment's real timing untouched. The fix is surgical: a single bad segment
degrades only that segment, proven by dedicated regression tests asserting
the *other* segments' boundaries and words are byte-identical to what ADR
0065 alone would have produced. ADR 0065's all-or-nothing principle is
preserved exactly for the failure category it was designed for (total
alignment failure); this ADR adds a second, appropriately-scoped response
for a different failure category (per-segment implausibility) rather than
stretching or weakening the first one.

**Negative / deferred.** This does not fix aeneas's alignment accuracy or
confirm the pure-Python-fallback root cause #154 flagged as the likely
cause — it only prevents the worst visible symptom of whatever an aligner
reports. The 8.0 wps threshold is a reasoned, documented choice with a large
margin, not a measured optimum; revisit if a real, unusually fast narration
style is ever legitimately flagged (no evidence of this in #154's testing).
The consecutive-run zero-width behavior (§3) is an accepted, documented
simplification for a case with no observed real-world instance.

**Risks.** Low and bounded. `_implausible_segment_indices` and
`_anchor_implausible_segments` are both pure and covered by fixture-only
unit tests for every branch (threshold boundary, zero-duration guard,
empty-list skip, single/middle/consecutive implausible segments); the
`MediaPipeline.build` wiring is covered by build-level regression tests
proving the surgical (not whole-narration) nature of the fix, the pathological
all-implausible fallback, and confirmation that every ADR 0065 total-failure
path (exception, count mismatch, empty word list, real overlap) and the
no-aligner-configured path are untouched.

## Alternatives considered

- **Reuse ADR 0065's whole-narration fallback as-is (the option #154 itself
  flagged as "simpler").** Rejected for the reason detailed in §Relationship
  to ADR 0065 above: #154 proved the failure is positional and would fire on
  nearly every real render, permanently disabling real cue-boundary
  derivation for the entire fleet.
- **A lower or stricter threshold (e.g. 5-6 wps).** Rejected: #154's own
  "payoff" example measured 6.5 wps and is explicitly called plausible in the
  issue; a threshold that close to real fast speech risks false-triggering on
  legitimately quick narration. 8.0 wps leaves a wide, deliberate margin with
  no cost, since the confirmed failure mode is over an order of magnitude
  past it.
- **Split the gap evenly across a consecutive run of implausible segments**
  (rather than the first-absorbs / rest-collapse-to-zero behavior). Rejected
  for now: adds real complexity for a scenario with zero observed instances
  (#154's evidence is always a single crushed segment); the simpler rule
  already satisfies every invariant (non-overlapping, ordered, full
  coverage). Revisit if a future real render ever exhibits multiple
  simultaneous implausible segments.
- **Fix the root cause instead (enable aeneas's C extensions, or swap to a
  higher-accuracy aligner such as WhisperX).** Both remain valid, tracked
  follow-ups (§Context) but are out of scope here: this ADR is a defensive
  guard against *whatever* an aligner reports, independent of which aligner
  or build is in use, mirroring ADR 0065's own stance of fixing the
  boundary-derivation logic without validating alignment accuracy.

## References

- Issue #154 (this ADR's fix; the three isolating tests and the measured
  crush numbers)
- Issue #152 / ADR 0065 (the cue-boundary-derivation fix this ADR extends;
  the all-or-nothing principle this ADR reconciles with rather than
  contradicts), issue #153 (the PR whose live re-verification surfaced #154)
- Issue #151 (real `WordAligner` wiring), issue #150
  (`SegmentedTTSProvider`'s real inter-sentence silences — ruled out as the
  cause via #154's first isolating test), issue #146 (the original bug
  report; stays open pending full live re-verification)
- ADR 0062 (word-level karaoke forced alignment — the seam this ADR's guard
  sits downstream of; its own honesty section already flags alignment
  accuracy as unvalidated on real hardware), ADR 0059 (ASS captions /
  `_format_karaoke_body`'s clamp and the plain cue-fade a word-free cue
  degrades to, both untouched), ADR 0061 (the four-beat retention arc whose
  LOOP beat is why #154's positional failure matters on every real render)
