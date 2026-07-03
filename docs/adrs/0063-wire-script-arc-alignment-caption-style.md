# ADR 0063: Wire the full script arc, real word alignment, and `caption_style` into the production pipeline

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

A 4-track parallel planning council (working an unrelated P2 feature epic) and a
user bug report independently converged on the same finding: three already-
shipped, already-tested capabilities had **zero wiring into the real production
path** (`VideoPipeline` → `MediaPipeline` → `CompositionService`):

1. `ScriptBuilder`'s full HOOK → BUILD → PAYOFF → LOOP retention arc (ADR 0061,
   issue #134) — `grep`-confirmed **zero callers outside `app/scripting/`**.
   `VideoPipeline.create_bundle` fed `MediaPipeline` the raw `CreatorPacket`;
   `MediaPipeline` split `narrative.script_outline` itself and never touched
   `packet.hooks` or `ScriptBuilder` at all. The hook and the closing loop
   re-hook were *produced* by the Short-Form Content Strategist and *scriptable*
   by `ScriptBuilder`, but never spoken or captioned in a real render.
2. `MediaPipeline`'s `word_aligner` parameter (ADR 0062, issue #136) — the
   constructor argument existed and was fully tested (`FakeWordAligner`, the
   degrade-on-failure path, the pure `AeneasAligner` argv/parser), but no code
   path anywhere constructed a real `AeneasAligner` and passed it in. The
   composition root (`app/services/composition.py`) never referenced the
   alignment package.
3. `caption_style` (ADR 0059, issue #131) — `CompositionService.render` and
   `MediaPipeline.build`'s call into it both accept the parameter, but
   `MediaPipeline.build` never had a `caption_style` parameter of its own to
   receive a non-default value from a caller, so every real render used
   `DEFAULT_CAPTION_STYLE` with no way to override it short of monkeypatching.

This is issue **#149**, the first concrete slice of the P0 "wire the spine"
umbrella (issue #148): closing the gap between "merged and hermetically green"
and "the pipeline actually does what its own components can do." The three
gaps are wired together in one PR because they touch overlapping code
(`MediaPipeline.build`'s signature, `VideoPipeline.create_bundle`, the
composition root's `MediaDeps`) — doing them separately would create rebase
conflicts for no benefit.

**Root cause, named plainly (per CLAUDE.md §11):** component-by-component
delivery shipped each capability with hermetic tests proving the *component*
works, but no test exercised the *production call path* end to end with that
capability engaged. `tests/services/video/test_pipeline.py` — the one file
that *should* have caught this — used a strategist fixture with **no hooks**,
so it never exercised `packet.hooks` at all, and never called `MediaPipeline`
with a non-default `caption_style` or a `word_aligner`. Hermetic-green was not
"the wiring exists"; it was "the components exist and are individually
correct." This ADR's tests specifically target the *wiring*, not the
components (which already had their own coverage).

## Decision

### 1. `VideoPipeline.create_bundle` now scripts the full arc before rendering

`VideoPipeline` gets a private `ScriptBuilder()` instance (pure, stateless,
CLAUDE.md §4 — no injection needed, mirroring how `MediaPipeline` defaults its
own `DeterministicSubtitleService`). `create_bundle` now:

```
narrative = self._select_narrative(packet, narrative_index)   # unchanged (title, B-roll query)
visual_uris = await self._retrieve_visuals(narrative.title)   # unchanged
script = self._script_builder.build(packet, narrative_index=narrative_index)
plan = await self._media.build(
    packet, narrative_index=narrative_index, visual_uris=visual_uris,
    segments=[beat.text for beat in script.beats],             # NEW
    caption_style=caption_style,                                # NEW
)
```

This is a **real, visible behavior change**, stated plainly rather than hidden
in a signature tweak: renders are now longer (they include the hook and loop
beats), and the hook/loop text is now heard and captioned where before it was
silently dropped after being produced. It also makes `packet.hooks`
**load-bearing** for a real render for the first time: `ScriptBuilder.build`
raises `ScriptBuilderError` if `packet.hooks` is empty, even when the packet
has a perfectly good narrative. Before this change, a hookless-but-narratable
packet rendered fine (narrative-only); after, it hard-fails. A well-formed
packet always has at least one hook (CLAUDE.md §5.4 — hooks are one of the
Short-Form Content Strategist's three required outputs), so this should not
bite a correctly-functioning research run, but it is a new failure mode worth
naming rather than discovering later. `ScriptBuilderError` propagates
un-wrapped from `create_bundle` (the same "not re-wrapped, cause stays
legible" posture `VideoPipelineError`'s docstring already documents for
`MediaPipelineError`/`CompositionError`).

### 2. `MediaPipeline.build` gains `segments` — an explicit beat-text override

```python
async def build(
    self, packet, *, narrative_index=0, visual_uris=None,
    segments: list[str] | None = None,              # NEW
    caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE,  # NEW
) -> MediaPlan: ...
```

`segments=None` (the default) reproduces the *exact* pre-existing behavior:
`narrative.script_outline` is split by the unchanged `_split_into_beats`. When
the caller supplies `segments` explicitly, the pipeline narrates/captions
*those* texts instead — `_split_into_beats` and `_allocate_timings` keep their
existing internal logic untouched; only what feeds them changes. This keeps
`app/media/` decoupled from `app/scripting/` (the "copy, don't cross-import"
convention between sibling packages, ADR 0019 §4): `MediaPipeline` accepts a
plain `list[str]`, never a `ShortScript`/`ScriptBeat` type, so no new
cross-package import is introduced into the media layer. `VideoPipeline`
(the orchestration layer that already legitimately imports both
`app.media.pipeline` and `app.schemas.research_state` — see ADR 0025's
documented exception) is the one place that imports `ScriptBuilder` and
flattens its beats to plain strings before handing them to `MediaPipeline`.

`caption_style` is passed straight through to
`self._composition.render(..., caption_style=caption_style)`, closing the gap
where the parameter existed on `render()` but had no path in from a real
caller.

### 3. `Settings.aeneas_python_bin` — optional aeneas provisioning, Kokoro-pattern

```python
aeneas_python_bin: str | None = None
```

Mirrors `kokoro_model_path`/`kokoro_voices_path` exactly: a bare path setting,
`None` by default, env var `REEL_AUTOMATION_AENEAS_PYTHON_BIN`, no validation
beyond what Pydantic gives for free (a bad path fails loud at `align()` time —
`AlignmentError`, per ADR 0062 — not at settings-load time). `MediaDeps` gains
a matching `word_aligner: WordAligner | None = None` field. `build_media_deps`
constructs a real `AeneasAligner(python_bin=...)` and wires it into
`MediaDeps.word_aligner` iff `aeneas_python_bin` is set; `VideoPipeline.__init__`
forwards `media_deps.word_aligner` into the `MediaPipeline` it constructs.
Unset (the default, unconditionally true for every environment before this
PR), the whole chain reproduces `word_aligner=None` — today's cue-level-only
captions, byte-for-byte unchanged. aeneas owns no network client (a subprocess
contract, not an httpx seam — ADR 0062), so it adds nothing to `MediaBundle`'s
`closables`.

### Package boundary check (explicit, per ADR 0019 §4)

`app/media/` still imports nothing from `app/scripting/`; `app/scripting/`
still imports nothing from `app/media/`. The only new cross-package edge is
`app/services/video/pipeline.py` (already the designated orchestration seam
per ADR 0025) importing `ScriptBuilder` — an addition to an existing,
documented integration point, not a new architectural boundary.

## Honesty notes (explicit, per CLAUDE.md §11)

- **`caption_style` is threaded, not yet *sourced*.** This PR makes the
  parameter flow end to end from any caller down to `render()`. It does
  **not** decide *where* a non-default style would come from in a real run —
  there is no `ChannelProfile` binding yet, and that is an explicitly
  undecided, separate architecture question (out of scope here, same as the
  brief for #149 states). Every real caller today still gets
  `DEFAULT_CAPTION_STYLE` unless it passes something else explicitly.
- **aeneas is now *constructible* in the live composition root, still
  timing-unvalidated.** `aeneas_python_bin` lets an operator opt a live
  `AeneasAligner` into the router; this closes the "nobody ever builds one"
  gap ADR 0062 explicitly deferred. It does not change ADR 0062's own honesty
  posture: alignment accuracy and the visual karaoke result are unverified
  until a real machine runs it against real narration end to end (the
  documented last-mile follow-up, CLAUDE.md §13).
- **`ScriptBuilder` makes `packet.hooks` load-bearing.** See Decision §1 — a
  real, named behavior change, not a silent tightening.

## Consequences

**Positive.** The three highest-value already-shipped capabilities the P2
planning council and the #146 bug report flagged as "built but disconnected"
are now on the real render path. A real run narrates and captions the full
retention arc (the actual point of ADR 0061), can opt into real word-level
karaoke timing with one setting (the actual point of ADR 0062), and can be
handed a non-default caption style by any future caller (the actual point of
ADR 0059) without further plumbing. No public signature loses backward
compatibility: every new parameter is additive-keyword-defaulted, and every
default reproduces pre-existing behavior exactly (locked by regression tests).

**Negative / deferred.** `ScriptBuilderError` is a new failure mode for
`VideoPipeline.create`/`create_bundle` (hookless packets now hard-fail instead
of silently rendering narrative-only) — a deliberate, documented tightening,
not a regression, but a real behavior change a caller could observe.
`caption_style` still has no real source (`ChannelProfile` or equivalent) —
this PR closes the "stranded parameter" gap, not the "who picks the style"
question. aeneas's timing accuracy remains unvalidated pending a live run.

**Risks.** Low and bounded. Every new parameter is optional and
default-preserving; the full-arc regression test asserts the before/after
`MediaPlan.script_segments` shape directly (hook text first, default loop text
last) rather than a proxy metric, so a future refactor that silently drops the
hook/loop again would be caught. The `word_aligner`/`caption_style` wiring is
covered at both the `Settings`/`build_media_deps` layer and the
`VideoPipeline`-constructs-`MediaPipeline` layer, closing exactly the kind of
"component exists but nothing wires it" gap this ADR exists to fix.

## Alternatives considered

- **Pass a `ShortScript`/`ScriptBeat` type into `MediaPipeline.build` instead
  of `list[str]`.** Rejected: it would require `app/media/` to import
  `app/scripting/`, breaking the documented sibling-package decoupling (ADR
  0019 §4's "copy, don't cross-import" convention) for a purpose (carrying
  beat *roles* into the media layer) nothing downstream consumes yet — the
  media layer narrates/captions generically regardless of beat role, exactly
  as ADR 0061 already decided. A plain `list[str]` is the minimal seam.
- **Silently drop hookless packets to narrative-only instead of raising.**
  Rejected: that would re-introduce exactly the silent-gap failure mode this
  ADR exists to close, just one layer down — a hookless packet would render
  "successfully" while quietly skipping the retention arc's most important
  beat. Raising `ScriptBuilderError` (already `ScriptBuilder`'s own contract
  for a missing hook) is the honest, `CLAUDE.md`-consistent choice.
  `CreatorPacket`s are expected to have a hook (§5.4), so this should be rare
  in practice against a correctly functioning research run.
- **Auto-select/build a default `ChannelProfile`-sourced `caption_style` in
  this PR.** Rejected as scope creep: no `ChannelProfile` binding architecture
  exists yet, and inventing one under this issue's umbrella would conflate an
  undecided, separate architecture question with a narrow wiring fix.
- **Make `aeneas_python_bin` validate the interpreter/aeneas import at
  settings-parse time.** Rejected: it would make `Settings()` construction
  perform filesystem/subprocess I/O (a hermetic-test and startup-latency cost)
  for a value that is only exercised by a real render; `AlignmentError` at
  `align()` time is the existing, documented failure surface for a bad aeneas
  environment (ADR 0062).

## References

- Issue #149 (this ADR), issue #148 (P0 "wire the spine" umbrella), issue #146
  (the bug report this addresses the root cause of)
- ADR 0061 (4-beat retention arc — the arc this PR finally renders), ADR 0062
  (word-level karaoke forced alignment — the aligner this PR finally
  constructs), ADR 0059 (ASS captions / `CaptionStyle` — the style this PR
  finally threads), ADR 0025 (the `VideoPipeline`/`MediaPipeline` handoff seam
  and its documented cross-layer exception), ADR 0019 (the media package's
  "copy, don't cross-import" sibling-package convention), ADR 0050 (the
  Kokoro optional-provisioning pattern `aeneas_python_bin` mirrors)
