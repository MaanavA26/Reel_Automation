# ADR 0060: Machine-enforceable Definition-of-Done QC gate

- **Status:** Accepted
- **Date:** 2026-07-01
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

The repository can produce a render end-to-end, but nothing yet *checks the
finished video against an objective quality bar*. The Definition-of-Done rubric
(the owner's quality verdict on the prototype: 1.6/10 — late hook, no burned-in
captions, quiet audio, static visuals, no payoff) is currently prose, not code.
For autonomous publishing to be safe, the DoD must be **machine-enforceable**: a
deterministic gate that measures a `RenderedVideo` and refuses to auto-publish a
clip that misses the bar. This is P1 Step 3 of the creative-quality overhaul
(epic #125 / issue #126), following audio mastering (ADR 0058) and captions
(Step 2).

This is deterministic **tool** work (CLAUDE.md §4): every check is a comparison
of a measured value against a fixed band — no judgment, no LLM. Several forces
shaped the design, locked by the planning council + owner:

- **One source of the DoD numbers (spine C2)** and **one source of the loudness
  primitives (spine C1).** The numeric bands cannot be scattered, and the
  loudness target/measure-pass/parser already live in `composition.loudness`
  (ADR 0058) — the gate must *reuse* them, not duplicate them.
- **SKIPPED is not PASS.** A check we could not run (a missing measurement, a
  deferred dependency) must be a distinct, non-green outcome — the canonical way
  a "quality gate" silently lies is to count an un-run check as a pass.
- **The gate measures the *rendered output*, not the source.** ADR 0058 masters
  the *source* narration; this gate verifies the *muxed master* actually hit
  target — the end-to-end half of that loop.
- **The pure `PrePublishGate` (ADR 0041) stays I/O-free.** That gate is a pure
  function over content caveats; it must never grow an ffprobe/subprocess call.
  Mapping QC findings to a publish decision is a *separate* policy.
- **No new heavy dependencies.** No optical-flow/scene-detect library for cut
  rhythm; no tesseract/OCR for caption placement.

## Decision

### 1. A new `backend/app/media/qc/` package, four concerns separated

- **`rubric.py` — the single source of DoD bands (C2).** `QCRubric` is a frozen,
  fully-defaulted dataclass (the shorts DoD out of the box) with
  `from_mapping`/`from_json` constructors so a **per-channel skin tunes a band
  from a JSON spec without a code change**; unknown keys fail loud (a typo'd
  channel spec must not silently ship at the default). The loudness/true-peak/
  sample-rate targets are **re-exported** from `composition.loudness`
  (`TARGET_I`, `TARGET_TP`, `OUTPUT_SAMPLE_RATE`), never redefined (C1) — the
  rubric only adds the *tolerances* and the QC-specific bands.
- **`report.py` — the tri-state result DTO.** `QCCheckStatus` is an `IntEnum`
  `PASS < SKIPPED < FAIL` (mirroring `Severity` in `safety/verdict.py`) so the
  overall is `max(...)` over the per-check statuses. `QCReport` carries the
  ordered per-check `QCCheckResult`s plus a **code-derived** `QCSummary`
  (`overall` + passed/skipped/failed counts) via `QCReport.summarize` — the model
  never votes on the rollup. `summary.passed` is strict-green: overall is exactly
  PASS (any SKIPPED means *not fully verified*).
- **`probe.py` — the only I/O seam.** A `QCProbe` Protocol + a binary-backed
  `FfmpegQCProbe` + a `FakeQCProbe`, mirroring the composition seam. It reuses
  `build_loudnorm_measure_args` + `parse_loudnorm_stats` **verbatim** (C1 — a
  loudnorm measure pass on a video input measures its audio track), and adds a
  pure `ffprobe -show_streams` argv builder + parser for the audio sample rate
  and the soft-subtitle-stream flag. The output is a strict `QCMeasurement`. Live
  ffprobe/loudnorm runs behind `@pytest.mark.integration`.
- **`service.py` — the pure evaluator.** `QCService.evaluate(video, captions,
  measurement)` runs every check (no short-circuit) and returns the raw
  `QCReport`. Pure over the handed-in `QCMeasurement` — no I/O, hermetically
  testable with directly-built inputs.

### 2. The checks, and what each measures

`LENGTH_BAND` (video duration in the band), `INTEGRATED_LOUDNESS` / `TRUE_PEAK` /
`AUDIO_SAMPLE_RATE` (from the reused `LoudnessStats` + the probe's sample rate),
`FIRST_WORD_ONSET` (first caption cue start ≤ budget), `SCRIPT_PACE` (caption
words ÷ rendered duration, in the wpm band), `CUT_RHYTHM` (longest `edit_list`
segment ≤ ceiling), `CAPTION_PRESENCE` (track non-empty + coverage ÷ **video**
duration), `CAPTIONS_BURNED_IN`, `CAPTION_SAFE_ZONE`.

The "density" tautology the brief warned against is dropped: caption coverage is
measured against the **video** duration (a track that ends early FAILs), not
against the track's own span (always 1.0, can never fail).

### 3. `edit_list` — the hermetic cut-rhythm source, not optical flow

A new **additive** `RenderedVideo.edit_list: list[(start_ms, end_ms)]` records
the deterministic per-visual equal slices the renderer already lays out
(`build_edit_list`, the same equal-slice math `build_ffmpeg_args` uses, at ms
precision tiling `[0, duration]` with no drift). One visual → one full-length
segment (zero cuts), so a static image correctly FAILs `CUT_RHYTHM`; N visuals →
N abutting segments. Defaulted to `[]` so existing constructions don't break — an
empty list is "cut structure unknown" → **SKIPPED**, never a false pass. This
supersedes the issue's optical-flow/scene-detect idea: the renderer already knows
its cut points exactly, so no second analysis (and no new dependency) is needed.

### 4. Tri-state semantics: SKIPPED vs PASS vs FAIL

- **SKIPPED** (not PASS): the first-VO-word *half* of `FIRST_WORD_ONSET`
  (`SynthesizedSpeech` has only `duration_ms`, no word timing) — note the check as
  a whole still PASS/FAILs on the *caption-onset* half it **can** measure, with
  the VO-word skip folded into the detail string (measure what's measurable, don't
  discard the caption signal); only when there are **no caption cues** does the
  whole `FIRST_WORD_ONSET` go SKIPPED. `CUT_RHYTHM` with
  no `edit_list`; `SCRIPT_PACE` with no narration; `CAPTION_SAFE_ZONE` (OCR
  deferred, §6). A SKIPPED check makes the overall SKIPPED (not green).
- **FAIL** (a real, measurable miss): an out-of-band length/loudness/pace, a late
  first caption, an under-cut visual, an empty/low-coverage caption track, and —
  today — `CAPTIONS_BURNED_IN`.

### 5. The QC gate policy is separate; the pure `PrePublishGate` stays I/O-free

`gate.py` holds `QCGatePolicy` + `QCGate`, mirroring `GatePolicy`/`PrePublishGate`:
it maps a raw `QCReport` to a publish decision (FAIL → REVIEW by default, or BLOCK
for `hard_fail_checks`; SKIPPED → REVIEW; all-PASS → ALLOW) and the decision is
the max severity over the per-check reasons. **The decision vocabulary is reused,
not re-minted:** `QCGate` imports `SafetyDecision`/`Severity` from
`app.safety.verdict`. This is a narrow, conscious `media → safety` import of the
*decision enums only* (not the safety gate's logic) — a second parallel
ALLOW/REVIEW/BLOCK enum in the media layer would be drift, not decoupling. The
pure `PrePublishGate` is untouched: no ffprobe/subprocess leaks into
`safety/gate.py`.

### 6. Deferred-by-design, stated honestly

- **`CAPTIONS_BURNED_IN` FAILs hermetically today (C4).** This sandbox has no
  libass, so the renderer soft-muxes captions as a `mov_text` track; a present
  soft subtitle stream in the output is the *not-burned-in* signature, so the
  check FAILs — correctly keeping autonomous mode REVIEW-locked until libass is
  provisioned. It verifies the **absence of the soft-mux signature**, not literal
  pixels (pixel verification is OCR, deferred). It PASSes when no soft subtitle
  stream is present (libass landed, captions in pixels) — asserted in tests.
- **`CAPTION_SAFE_ZONE` is SKIPPED (OCR deferred, owner decision D3).** Verifying
  captions sit inside the title-safe margin needs pixel/OCR (tesseract), a
  dependency the owner deferred. Rather than fake a PASS, the check resolves to
  SKIPPED with a clear deferral detail and the gate policy routes it to REVIEW.
- **Captions are cue-level fade, not word-karaoke "animated."** The QC gate makes
  no "animated captions" claim — `CAPTION_PRESENCE`/`CAPTIONS_BURNED_IN` are
  measurable and fair; "animated" is not measurable here and is out of scope.
- **Hermetic-green ≠ a real render passed QC.** The loudness/sample-rate/
  burned-in checks against a *real* file run only under `@pytest.mark.integration`
  (skips without ffmpeg/ffprobe). The hermetic suite exercises the evaluator's
  logic over handed-in measurements; it does not prove a real master hit target.

### 7. Capability before wiring

The QC service/probe/gate are built and tested but **not yet wired** into the
pipeline/closed-loop runner (the ADR 0040/0051 capability-before-wiring
precedent). The integration point — probe the rendered file, evaluate, route the
`QCGateVerdict` alongside the safety verdict into the review/publish decision — is
a documented follow-up.

## Consequences

**Positive.** The DoD is now code: a single rubric (one source, per-channel
tunable from JSON), a tri-state report that cannot count an un-run check as green,
and a probe that measures the *rendered master* reusing the exact loudness
primitives the renderer mastered with (C1/C2 — producing and checking cannot
drift). `edit_list` gives hermetic cut-rhythm with no optical-flow dependency. The
publish-decision split keeps the pure `PrePublishGate` pure. Stdlib + Pydantic +
the already-required ffmpeg/ffprobe — no new dependency.

**Negative / deferred.** `CAPTION_SAFE_ZONE` is SKIPPED until OCR lands;
`CAPTIONS_BURNED_IN` FAILs until libass is provisioned; the first-VO-word onset is
SKIPPED until word-level VO timing exists. The gate is not yet wired into the
runner. Live measurement is only exercised under the integration marker.

**Risks.** Low and bounded. The evaluator is pure and fully hermetic (each check
at its band boundary, the tri-state rollup, the rubric JSON round-trip + a
per-channel override flipping a verdict, the `edit_list` 1-and-N population, the
burned-in FAIL/PASS toggle, the gate-policy severity mapping); the binary-backed
probe is integration-gated. The one new schema field (`edit_list`) is additive and
defaulted, so no existing `RenderedVideo` construction breaks.

## Alternatives considered

- **Optical-flow / scene-detect for cut rhythm.** Rejected: the renderer already
  computes exact cut points (the equal-slice layout), so a second analysis adds a
  heavy dependency and a source of disagreement for zero gain. `edit_list` is the
  honest, hermetic source.
- **A `captions_burned_in` flag recorded on `RenderedVideo`.** Rejected: a
  self-reported flag is the "contrived pass" the quality bar forbids — it asserts
  burn-in rather than verifying it. Probing the output for the soft-mux signature
  is output-centric and FAILs honestly today.
- **Caption coverage against the track's own span.** Rejected: always 1.0, can
  never fail — a tautological check. Coverage is measured against the video
  duration.
- **A new ALLOW/REVIEW/BLOCK enum in the media layer.** Rejected: the publish
  vocabulary already exists in `safety/verdict.py`; a parallel enum is drift. A
  narrow import of the decision enums is the honest single source.
- **Putting the QC→publish mapping inside `PrePublishGate`.** Rejected: it would
  force ffprobe/subprocess (or its result) into a gate that is pure by contract
  (ADR 0041). The QC gate policy is the correct home.
- **Counting an un-runnable check as PASS (binary pass/fail).** Rejected: that is
  exactly how a quality gate silently lies. The tri-state with SKIPPED ≠ PASS is
  the load-bearing decision of this ADR.
