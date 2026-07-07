# ADR 0066: Per-segment karaoke plausibility guard (word-data only)

- **Status:** Accepted
- **Date:** 2026-07-06 (revised 2026-07-07: boundary anchoring removed after
  the independent re-review of PR #155 proved it a mathematical no-op — see
  §Decision 3, the central rationale of this revision)
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

**Root cause (confirmed direction, tracked in #154, out of scope here):**
#154's follow-up testing confirmed cumulative DTW drift over long audio —
aligning the same tail content as a SHORT, isolated task produced a sane
1.6-second window (up from 52ms) at a plausible 6.9 wps. The actual boundary
fix is therefore **chunked per-beat alignment** (align each beat's audio
slice independently, so cumulative drift has nowhere to accumulate) — a
bigger design change that #154 tracks as the root-cause follow-up. This ADR
does **not** attempt it; it only fixes how the pipeline uses whatever an
aligner reports, exactly as ADR 0065 fixed how a *given* alignment result
becomes cue boundaries without validating the alignment's accuracy itself.

### Severity — what this guard does and does not address

Every real video's last beat is the LOOP (the deliberate retention/re-hook
callback, ADR 0061) — the systematic tail-crushing means the loop's karaoke
word data is garbage on every single real render, not an edge case. The
crushed cue produced **two** distinct symptoms:

- a **garbled karaoke sweep**: 11 words' `\kf` highlights racing through in
  ~52ms and then sitting frozen — *this* is what this ADR prevents;
- a **~52ms cue window**: the loop's caption has essentially no on-screen
  time — this ADR does **not** fix that (see §Decision 3); it is #154's
  chunked-alignment work.

## Relationship to ADR 0065

ADR 0065 established an explicit **all-or-nothing** principle for
`_derive_timings_from_alignment`: never partially mix derived and guessed
*boundaries* across different cues, because doing so "would reintroduce a
subtler version of the exact two-source-disagreement bug this function
exists to remove." That principle is untouched — stronger than before, in
fact, since this guard now changes **no boundary at all**: every cue's
timing comes from exactly one source (`_derive_timings_from_alignment`, or
`_allocate_timings` on whole-narration fallback), with zero per-segment
exceptions.

The narrow exception this ADR adds is on the **word-attachment** side only:
after a fully successful derivation, individual cues may render word-free
(their karaoke data discarded as implausible) while their neighbors keep
real word timings. That does not violate the two-source principle — a
word-free cue has no second timing source to disagree with its boundary; it
simply degrades to the plain cue-level fade (ADR 0059) that every cue used
before #144 introduced karaoke at all.

ADR 0065's total-failure category (an aligner exception, a per-segment count
mismatch, or an empty per-segment word list) keeps its existing
whole-narration `_allocate_timings` fallback, unchanged. #154 is a
*different* failure category: alignment **succeeds** with a result the
derivation accepts as valid — the data is present, it is simply *wrong* for
one specific segment. Discarding every segment's real alignment to respond
to one bad segment would fire on nearly every real multi-beat render (the
crushed segment is reliably the last one, and there is always a last one),
permanently disabling real cue-boundary derivation and defeating
#151/#152/#153's purpose. The proportionate response is per-segment — and,
per §Decision 3, per-segment on the *words only*.

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
worse, would let this guard silently absorb a case ADR 0065 deliberately
treats as total failure.

### 2. The threshold: `MAX_PLAUSIBLE_WORDS_PER_SECOND = 8.0`

Sustained human speech — including fast TTS narration — does not exceed
roughly 4-5 words/second. `8.0` is a deliberately generous margin (roughly
double that ceiling) chosen so genuinely fast-but-real speech never
false-triggers this guard; #154's own "payoff" segment measured 6.5 wps and
is explicitly called out in the issue as "fast but plausible" — comfortably
under 8. #154's actual crushed segments imply 200+ words/second (11 words in
40-52ms), more than an order of magnitude past the threshold, so the margin
costs nothing in practice. The check is a strict `>` (a rate *equal* to the
threshold is not flagged) — tested directly at 8.0 wps (not flagged) and
8.008 wps (flagged).

**Known caveat:** on very short segments the words-per-second heuristic gets
noisy — a 1-word segment genuinely aligned under ~125ms already implies
> 8 wps and is flagged. The consequence is deliberately mild (§3: that cue
only drops its karaoke sweep; its timing and plain cue-level fade are
unaffected), so the false positive is accepted rather than special-cased.

### 3. The response is word-data only — boundaries are never changed

`cue.words` is set to `[]` for every flagged index, never the crushed span —
a cue with no words degrades to the existing plain cue-level fade (ADR
0059), exactly like a total-alignment-failure cue does today, and a warning
names the flagged segment indices. **No cue boundary is modified**: every
cue — flagged ones included — keeps exactly the timing
`_derive_timings_from_alignment` produced, locked by the
`test_plausibility_guard_never_alters_cue_boundaries` contract test.

This is the central decision of the 2026-07-07 revision, and it is a
*negative* result worth recording. PR #155's first draft also "re-anchored"
each flagged segment's boundary to its nearest plausible neighbors
(`start = previous cue's end`, `end = next plausible segment's start`, with
`0`/`total_ms` at the edges). The independent re-review proved that
computation is a **mathematical no-op for every isolated implausible
segment** — the only case #154 ever observed: `_derive_timings_from_alignment`'s
output is always contiguous (each cue's end equals the next cue's start, its
gap-bridging rule) and endpoint-pinned (first start `0`, last end
`total_ms`), so "the previous cue's end" and "the next plausible segment's
start" *are* the flagged cue's existing boundary, reproduced exactly. The
proof was replayed empirically on #154's exact measured numbers: the LOOP
cue came out `[24680, 24732]` — the same 52ms window — with or without the
anchoring. The only inputs where anchoring changed anything were
*consecutive runs* of implausible segments (where it produced zero-width
cues), a case never observed in reality. Shipping dead boundary logic while
claiming it as a fix mechanism failed review honesty; it was removed.

The consequence, stated plainly: **the crushed cue window persists.** #154's
LOOP cue is still ~52ms wide after this guard — the viewer now sees a clean
cue-level fade instead of a garbled karaoke flash, but still has essentially
no time to read it. The boundary fix is #154's chunked per-beat alignment
follow-up (empirically validated there: 52ms → 1.6s on the same tail
content), not this guard. A related review finding, also tracked in #154:
the QC gate (ADR 0060) has no minimum-cue-duration check, so a 52ms cue
currently passes QC silently.

### 4. Pathological edge case: every segment implausible

If `len(implausible) == len(word_lists)` — no evidence this occurs in
practice, per #154's own observations, which show a single crushed segment
in every case — there is no real alignment left worth preserving, so the
pipeline treats it exactly like a total alignment failure: the same
whole-narration `_allocate_timings` fallback ADR 0065 already uses, all cues
word-free, with a distinct log message so a log reader can tell it apart
from both the per-segment word-clearing and ADR 0065's own total-failure
message. This routing is kept (rather than uniformly clearing words on all
cues) because it is a two-line branch into a pre-existing path — no boundary
arithmetic — and because boundaries derived from alignment data that is
implausible *everywhere* are garbage end to end; `_allocate_timings`'s
character-count guess is strictly more trustworthy there.

## Consequences

**Positive.** The garbled karaoke sweep on a crushed segment — 11 words'
highlights racing through ~52ms of a cue — cannot recur: flagged word data
is discarded and the cue renders as a clean plain fade (ADR 0059). The guard
is surgical (only flagged segments lose words; every other segment keeps its
real boundary *and* words) and provably timing-neutral
(`test_plausibility_guard_never_alters_cue_boundaries`). ADR 0065's
single-timing-source principle is preserved without exception.

**Negative / deferred.** The #154 flash *symptom* is only partially
mitigated: a crushed cue's window is still ~50ms, so its caption remains
effectively unreadable — it just fails cleanly now. The real fix (chunked
per-beat alignment) and a QC minimum-cue-duration check are tracked in #154;
issues #146 and #154 both stay open. The 8.0 wps threshold is a reasoned,
documented choice with a large margin, not a measured optimum, and can
false-positive on 1-2-word segments (§Decision 2's caveat — accepted, since
the cost is only a dropped karaoke sweep). This ADR does not fix aeneas's
alignment accuracy.

**Risks.** Low and bounded. `_implausible_segment_indices` is pure and
covered by fixture-only unit tests for every branch (threshold boundary,
zero-duration guard, empty-list skip, mixed flagged/unflagged); the
`MediaPipeline.build` wiring is covered by build-level regression tests
proving words are cleared for flagged segments (last, middle, and multiple),
boundaries are byte-identical to the derivation's output whenever the guard
fires, the pathological all-implausible case widens to the unchanged
`_allocate_timings` path, and every ADR 0065 total-failure path (exception,
count mismatch, empty word list, real overlap) and the
no-aligner-configured path are untouched.

## Alternatives considered

- **Re-anchor flagged segments' boundaries to their nearest plausible
  neighbors (PR #155's first draft).** Removed in the 2026-07-07 revision:
  proven a mathematical no-op for every isolated implausible segment (the
  only observed case) by the independent re-review — see §Decision 3. Dead
  logic presented as a fix mechanism is worse than no logic.
- **Per-segment character-count-width fallback for flagged segments (#154's
  own recommendation 2).** Not adopted: it would genuinely move boundaries,
  but by mixing a guessed boundary into an otherwise-derived track it
  reintroduces exactly the two-source inconsistency ADR 0065 exists to
  prevent — and it would still be a heuristic patch over data the chunked
  per-beat alignment follow-up (#154) makes trustworthy at the source.
- **Reuse ADR 0065's whole-narration fallback for any implausible segment
  (the option #154 itself flagged as "simpler").** Rejected: #154 proved the
  failure is positional and would fire on nearly every real render,
  permanently disabling real cue-boundary derivation (§Relationship to ADR
  0065). Retained only for the all-implausible pathological case (§Decision
  4), where there is nothing real left to preserve.
- **A lower or stricter threshold (e.g. 5-6 wps).** Rejected: #154's own
  "payoff" example measured 6.5 wps and is explicitly called plausible in the
  issue; a threshold that close to real fast speech risks false-triggering on
  legitimately quick narration. 8.0 wps leaves a wide, deliberate margin with
  no cost, since the confirmed failure mode is over an order of magnitude
  past it.
- **Fix the root cause here (chunked per-beat alignment, aeneas C
  extensions, or a higher-accuracy aligner such as WhisperX).** Out of scope
  for this guard and tracked in #154 — chunked per-beat alignment is the
  empirically-validated lead (52ms → 1.6s). This ADR is a defensive guard
  against *whatever* an aligner reports, independent of which aligner or
  build is in use, mirroring ADR 0065's own stance of fixing
  boundary-derivation logic without validating alignment accuracy.

## References

- Issue #154 (the bug this guard partially addresses — the three isolating
  tests and measured crush numbers; the chunked per-beat alignment
  validation; the root-cause follow-up tracking; **stays open**)
- PR #155's independent re-review verdict (2026-07-07) — the no-op proof and
  empirical replay behind §Decision 3's removal of boundary anchoring
- Issue #152 / ADR 0065 (the cue-boundary-derivation fix this ADR sits on
  top of; the all-or-nothing principle this ADR now preserves without any
  boundary exception), issue #153 (the PR whose live re-verification
  surfaced #154)
- Issue #151 (real `WordAligner` wiring), issue #150
  (`SegmentedTTSProvider`'s real inter-sentence silences — ruled out as the
  cause via #154's first isolating test), issue #146 (the original
  caption-sync bug report; **stays open**)
- ADR 0062 (word-level karaoke forced alignment — the seam this ADR's guard
  sits downstream of; its own honesty section already flags alignment
  accuracy as unvalidated on real hardware), ADR 0059 (ASS captions and the
  plain cue-fade a word-free cue degrades to, both untouched), ADR 0060 (the
  QC gate that currently lacks a minimum-cue-duration check — flagged in
  #154), ADR 0061 (the four-beat retention arc whose LOOP beat is why #154's
  positional failure matters on every real render)
