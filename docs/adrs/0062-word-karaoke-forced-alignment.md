# ADR 0062: Word-level karaoke captions via a `WordAligner` forced-alignment seam

- **Status:** Accepted
- **Date:** 2026-07-02
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none (extends 0059)
- **Superseded by:** none

## Context

P1 Step D2 of the creative-quality overhaul (epic #125 / issue #136). The analyst
verdict named **animated (word-level) captions** the single highest-leverage
retention fix, and Step 2 (#132, ADR 0059) shipped **cue-level fade only**,
explicitly deferring word karaoke to "§D2 … once word timings exist". This step
delivers that timing source and the karaoke emission.

The owner decision (2026-07-01) is **forced alignment** — real measured word
times, chosen over a deterministic character/syllable estimate for accuracy. The
hardware-safe realization of that decision is constrained by the build machine
(an 8GB M2 Air, non-negotiable): the default aligner must be CPU-light with no
neural model. **aeneas** (DTW over MFCCs against an eSpeak-synthesized
reference) fits; **WhisperX** (higher accuracy, ~1GB+ Whisper model) does not
and is explicitly out of this PR — a follow-up adapter behind the same seam,
opt-in for bigger machines only.

Everything here is deterministic **tool** work (CLAUDE.md §4): the aligner
measures where words land; the formatter maps timings to ASS tags; nothing
judges or plans. The step extends three established patterns rather than
inventing new ones: the provider seam (Protocol + adapters + Fake, ADR
0003/0019), the construction/execution subprocess split (ADR 0023, the QC probe
0060), and the pure `format_ass` formatter (ADR 0059).

## Decision

### 1. `WordSpan` DTO + an additive `Caption.words` carrier (`media.schemas`)

A strict `WordSpan` (`text`, `start_ms`, `end_ms`; integer ms on the narration
clock) and an **optional, default-empty** `words: list[WordSpan]` on `Caption`.
The existing `CaptionTrack` therefore carries word timings end to end — through
`MediaPlan`, `render()`, and into `format_ass` — with **no signature change
anywhere** and every pre-existing construction still valid (the
`RenderedVideo.edit_list` additive-field precedent, ADR 0060). The plain
SRT/VTT formatters ignore `words`.

Naming: issue #136 sketched `WordTiming (word, …)`; the shipped shape is
`WordSpan (text, …)` so the field name mirrors `Caption.text` (house DTO
naming) and the type name says what it is — a timed span nested in a cue. Like
`Caption`, `end_ms >= start_ms` is enforced by the formatter, not the DTO.

### 2. A `WordAligner` seam (`app/media/alignment/`)

`base.py` ships the **async** Protocol (I/O-bound provider contract, ADR
0002/0003 — real alignment is subprocess I/O):

```
align(*, audio_path: str, segments: Sequence[str]) -> list[list[WordSpan]]
```

- ``audio_path`` is a local path **or** ``file://`` URI — the pipeline passes
  `SynthesizedSpeech.audio_uri` through; resolution is the adapter's concern
  and fakes ignore it.
- The return is **per-segment** word-span lists, parallel to `segments`.
- `AlignmentError` is the seam's single normalized failure type (symmetric with
  `CompositionError` / `QCProbeError`).
- `split_words` (whitespace tokenization, punctuation attached) is the **one
  shared tokenization rule** — the unit aeneas gets one-per-line, the unit the
  fake stamps, and the unit `format_ass` sweeps — so aligner output always
  re-joins to the cue text.
- `FakeWordAligner` returns deterministic synthetic timings (a fixed
  `ms_per_word` cadence on one running clock — `FakeTTSProvider.ms_per_char` at
  word granularity) and records calls, keeping every pipeline/formatter test
  hermetic.

### 3. aeneas as an **external subprocess contract, not a pip dependency**

`aeneas.py` treats aeneas exactly like ffmpeg: a binary boundary, never an
import. aeneas drags numpy, compiled C extensions, and a **system eSpeak**
install; pinning it into `pyproject.toml` would bloat and destabilize the
hermetic build for a tool the engine only ever *executes*. Whoever wants live
karaoke installs aeneas into any interpreter (a dedicated venv is fine) and
points the adapter's `python_bin` at it — the ffmpeg posture.

The ADR 0023 split, verbatim:

- **`build_aeneas_task_args` (pure):** the
  `python -m aeneas.tools.execute_task <audio> <text> <config> <syncmap>` argv.
  Word-level granularity comes from the *input shape*, not a flag:
  `is_text_type=plain` makes one sync fragment per input line, and the adapter
  writes **one word per line**. `os_task_file_format=json` pins the output.
  The `language` code is validated against the config-string syntax (`|`, `=`,
  whitespace rejected) so a bad code fails loudly — the `font_name` discipline.
- **`parse_aeneas_syncmap` (pure):** JSON sync map → `(start_ms, end_ms)` per
  fragment. aeneas emits *second strings* ("0.480"); conversion is
  `round(seconds * 1000)` (exact recovery of millisecond-precision decimals —
  the ms→cs *truncation* lock belongs to the formatter, not here). Fail-loud on
  malformed input, `[]` for an empty fragment list.
- **`AeneasAligner.align` (execution):** resolves the audio URI (reusing
  composition's pure `resolve_local_path` — a conscious narrow reuse like the
  QC probe's loudness reuse, with `CompositionError` normalized to
  `AlignmentError`), writes the transient word list, runs **one** task over the
  whole narration via the single `_run` subprocess seam
  (`asyncio.to_thread`), checks the **fragment count equals the word count**
  (a mismatch is a broken contract, never silently mis-zipped), and slices the
  flat timings back into per-segment `WordSpan` lists. `_run` mirrors
  `FfmpegCompositionService._run`: argv list (no shell), missing
  interpreter / non-zero exit / timeout all normalized to `AlignmentError`
  with a stderr tail.

### 4. Karaoke emission in `format_ass` (`subtitles.base`)

A cue **with** `words` renders as sequential per-word `\kf` sweep syllables; a
cue **without** renders exactly the ADR 0059 cue-fade form — so a track with no
word timings anywhere is **byte-identical** to the pre-karaoke output (locked
by a golden test *and* verified against the actual `main` formatter during
development), and mixed tracks degrade per-cue.

`_format_karaoke_body` locks four rules:

- **Centiseconds, truncated, cumulative.** Word boundaries become cue-relative
  centiseconds via `// 10` (the same locked truncate-not-round decision as
  `_format_ass_timestamp`, ADR 0059), clamped monotonically into
  `[0, cue span]`. Each `\kf` duration is a *difference of cumulative
  boundaries*, so the emitted total telescopes to the last word's end and can
  never exceed the cue span — karaoke always fits the Dialogue line's window.
- **Gaps are empty-text spacer syllables.** The leading offset (cue start →
  first word) and inter-word gaps emit `{\kf<gap>}` with no text (silence
  sweeps nothing); zero-length spacers are omitted.
- **Clamp overhang, raise inversion.** Aligned word times and the pipeline's
  length-proportional cue boundaries are *independent* estimates, so mild
  overhang at cue seams is expected → clamped (degrade, never fail a render).
  A word whose own span is inverted (`end < start`) is a caller bug → raises,
  exactly like `_validate_cues`.
- **Same escaping; words are the text.** Word text passes through
  `_escape_ass_text`; words join with a trailing space on the preceding
  syllable; `cue.text` is not re-emitted on karaoke lines.

**`{\fad}` is kept on karaoke lines** (the locked composition decision):
`\fad` animates line *alpha* while `\kf` animates per-syllable *fill colour* —
independent channels libass composes without conflict — and keeping it gives
mixed tracks one uniform entrance/exit. Revisit only if the last-mile visual
check shows the reference look demands a hard snap.

**SecondaryColour becomes real.** `\kf` sweeps *from* SecondaryColour *to*
PrimaryColour, and the ADR 0059 row set them equal — a default karaoke sweep
would have been invisible. `CaptionStyle` gains `secondary_colour`
(`#808080` default, the dimmed unsung-word fill, validated like the other
colours), and the style row uses it **only when the track carries words**;
wordless tracks keep SecondaryColour == PrimaryColour so their output stays
byte-stable (libass consults SecondaryColour only for karaoke, so this is
purely text-stability).

### 5. Pipeline wiring: optional aligner, degrade-never-fail

`MediaPipeline` gains a keyword-only `word_aligner: WordAligner | None = None`.
`None` (the default everywhere, including the app composition root) is exactly
today's behavior. When present, alignment runs post-TTS on
(`audio.audio_uri`, the beat segments) and attaches spans to the cues — only
after the per-segment count check passes, so a failure can never half-attach.
**Any** exception logs a warning and degrades to cue-level captions (the broad
`except Exception` is deliberate at a provider-seam boundary — the TTS router's
fallback posture); alignment never fails a render.

The composition root does **not** inject `AeneasAligner` yet: enabling live
karaoke is one constructor argument, deferred to the last-mile validation run
(§Honesty).

### 6. QC: out of scope here

No `ANIMATED_CAPTIONS` QC check ships in this PR. Word timings now make that
DoD line *checkable*; coordinating a real assertion into the QC gate (ADR 0060)
is a tracked follow-up per #136.

## Honesty notes (explicit, per the brief)

- **aeneas is a documented-not-yet-live contract** (the ADR 0053 posture) until
  it runs on a real machine with aeneas + eSpeak installed. Hermetic tests
  cover the Fake, the exact argv, the sync-map parser, and the mocked `_run`
  seam; the `@pytest.mark.integration` test (skips when the tool is absent)
  exercises the live CLI against an ffmpeg-generated **tone** — a *contract*
  check, not an accuracy check.
- **Word-timing accuracy and the visual karaoke result are unvalidated.** No
  libass runs in CI and no real narration has been aligned; the "animated
  captions" DoD line becomes *implementable/checkable*, **not live-proven**.
  A live aligner run + a libass render check are last-mile follow-ups.
- **One-word-per-line DTW granularity is coarser than neural alignment.**
  aeneas aligns eSpeak-synthesized MFCCs; short function words can smear. The
  WhisperX adapter behind this same seam is the accuracy follow-up for
  channels that need tighter word pops.
- aeneas additionally requires **eSpeak** as a system dependency on whatever
  machine runs it — an install-time note, not a repo dependency.

## Consequences

**Positive.** The word-level "animated captions" look — the top DoD retention
line — is now one injected constructor argument away from a live render, with
the engine provider-neutral across aligners (fake today, aeneas on a real
machine, WhisperX later). No new pip dependency; no schema, protocol, or
signature breaks (`words` and `secondary_colour` are additive-defaulted); the
degraded path is byte-identical to ADR 0059 output and every failure mode ends
in a rendered video.

**Negative / deferred.** Karaoke quality is hostage to alignment quality, which
is unmeasured until the last mile; the aeneas env (separate interpreter +
eSpeak) is operational friction; the QC gate cannot yet assert the animated
line; `cue.text` and `cue.words` can in principle disagree (the pipeline
constructs them from the same segments, but the DTO does not enforce it).

**Risks.** Low and bounded. The formatter changes are pure and locked by exact
`\kf`-tag tests (rounding, spacers, clamping, escaping, the golden degrade);
the aligner is opt-in and never on the render's critical path (degrade path
tested); the subprocess contract mirrors two proven seams. The main real risk —
aeneas's CLI/JSON shape drifting from the documented contract — surfaces
loudly in the integration test the moment it runs on a real machine.

## Alternatives considered

- **Deterministic char/syllable timing estimate (no aligner).** Rejected by the
  owner decision: estimated word boundaries drift audibly from real speech and
  the karaoke look lives or dies on sync accuracy.
- **WhisperX as the default aligner.** Rejected for the default: it loads a
  ~1GB+ Whisper model — unsafe on the 8GB build machine. It remains the
  planned opt-in adapter behind this same seam.
- **aeneas as a pip dependency of this repo.** Rejected: numpy + compiled
  extensions + a system eSpeak for a tool we only execute; the ffmpeg
  subprocess posture keeps the hermetic build lean and the engine
  provider-neutral (CLAUDE.md §5.7 spirit).
- **A parallel `WordTrack` artifact instead of `Caption.words`.** Rejected:
  a second structure must be kept index-synced with the cue list through every
  hop (pipeline → plan → render → formatter), inviting drift; nesting spans in
  the cue makes the carrier self-consistent and changes no signatures.
- **`\k` (instant highlight) instead of `\kf` (sweep).** `\k` snaps the whole
  word at its start; `\kf` fills it across its measured duration — the
  reference karaoke look, and it visualizes the *duration* accuracy the
  aligner exists to provide. Both are trivially swappable in one f-string if
  the visual check prefers the snap.
- **Dropping `{\fad}` on karaoke lines.** Rejected for now: the channels are
  independent (alpha vs fill), and dropping it would make mixed
  (karaoke + degraded) tracks visibly inconsistent. Reversible after the
  last-mile visual check.
- **Re-deriving cue boundaries from aligned words.** Rejected: it would break
  the ADR 0025 invariant (`cues[-1].end_ms == audio.duration_ms`) that the
  pipeline and its consumers lock; word spans stay an additive layer inside
  allocated cues, with formatter-side clamping absorbing seam overhang.
- **Always emitting `secondary_colour` in the style row.** Rejected: it buys
  nothing visually on wordless tracks (libass only uses it for karaoke) and
  would break byte-stability of the degraded output.

## References

- Issue #136 (owner decision: forced alignment; aeneas default; WhisperX out),
  epic #125 (Tier-B creative quality)
- ADR 0059 (ASS captions; §D2 deferral this ADR resolves), ADR 0060 (QC gate;
  probe pattern), ADR 0023 (construction/execution split), ADR 0025 (pipeline
  timing invariant), ADR 0053 (documented-not-yet-live adapter posture)
- aeneas: <https://github.com/readbeyond/aeneas> (`aeneas.tools.execute_task`,
  task config `is_text_type=plain`, `os_task_file_format=json`)
- ASS karaoke tags (`\k`/`\kf`, centiseconds): the SSA/ASS spec as rendered by
  libass
