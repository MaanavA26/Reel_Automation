# ADR 0061: 4-beat retention arc — HOOK → BUILD → PAYOFF → LOOP

- **Status:** Accepted
- **Date:** 2026-07-01
- **Deciders:** Tech Lead, advisor
- **Supersedes:** 0038 (the hook → body → CTA script shape)
- **Superseded by:** none

## Context

ADR 0038 gave the `ScriptBuilder` *tool* its first structure: HOOK, one BODY beat
per outline line, then a claim-free CTA. That shape is retention-flat — a flat
run of interchangeable BODY beats with a "follow me" tacked on the end has no
built-in rising action, no distinct final-act resolution, and nothing that seams
the viewer back to the start. The Definition-of-Done rubric (the machine-checkable
quality bar distilled from the prototype's 1.6/10 verdict) calls for a **payoff /
loop** and a length band, not a CTA. This is **P1 Step 5** of the creative-quality
overhaul (epic #125 / issue #134).

The builder is a deterministic **tool**, never an agent (CLAUDE.md §4): the
creative *wording* is judgment and already happened upstream in the Short-Form
Content Strategist (`CreatorPacketAgent`, M12). This step changes only the
deterministic *structuring* — how the already-authored lines are labelled and
banded — with no LLM call and no judgment about *what* to say.

**Structure ≠ craft.** Labelling beats HOOK/BUILD/PAYOFF/LOOP makes the retention
arc explicit and machine-checkable; it does **not** make the hook stickier, the
payoff sharper, or the loop actually seam back to the opener — that is the upstream
writing model/prompt's job (a separate future step). This is the same honesty
discipline as "cue-fade ≠ animated captions": the tool mints no craft and does not
by itself satisfy the DoD's writing-quality bar. Overclaiming here would be exactly
the "AI magic" abstraction CLAUDE.md §11 forbids.

## Decision

### 1. `BeatRole` gains BUILD / PAYOFF / LOOP; BODY / CTA are deprecated, not removed

`BeatRole` (a `StrEnum`) adds `BUILD` (`"build"`), `PAYOFF` (`"payoff"`), and
`LOOP` (`"loop"`). The fixed construction order becomes: exactly one `HOOK`, then
zero or more `BUILD` beats, then exactly one `PAYOFF`, then exactly one `LOOP`.
(A multi-line narrative yields ≥1 `BUILD`; a single-line narrative yields none —
see Decision 2.)

`BODY` and `CTA` are **retained as deprecated values**, not deleted. A `StrEnum`
value may already be serialized inside a persisted `ShortScript` (JSON/DB); removing
the member would make those old records fail to deserialize (`ValueError` on the
unknown enum value). Keeping them is the backward-compatible choice (CLAUDE.md §9.2)
— they are documented as no-longer-emitted, and a round-trip regression test locks
this in.

### 2. `ScriptBuilder.build` emits the arc deterministically

The outline is split into topical lines by the existing `_split_into_beats`
(unchanged). Then:

- **≥2 topical lines** → all lines **except the last** become `BUILD` beats; the
  **last line** becomes the `PAYOFF`. The payoff is thus the final topical beat —
  the DoD "distinct payoff in the final act", realized *structurally* (a labelled
  position), not by rewriting the prose.
- **exactly 1 topical line** → that line is the `PAYOFF`; there is **no** `BUILD`
  beat (`HOOK → PAYOFF → LOOP`).
- The existing "no narratable beat → `ScriptBuilderError`" guard is preserved.

A claim-free `LOOP` beat replaces the old CTA. It is structural scaffolding: it
asserts nothing about the topic, so it carries no grounding (`finding_ids=[]`) and
is never `disputed` — the same §11 honesty exemption the CTA had, now on the LOOP.
Grounding/`disputed` derivation for HOOK/BUILD/PAYOFF is unchanged (reuses
`_make_beat`).

### 3. `loop_text` knob with a `cta_text` back-compat alias

A `loop_text` constructor knob (default `DEFAULT_LOOP_TEXT`, a concise re-hook that
seams back to the opener) sets the LOOP text. For backward compatibility, `cta_text`
is a **deprecated alias**: existing callers passing `cta_text=` keep working and it
fills the LOOP beat. Both are `None`-sentinelled so an explicit override is
distinguishable from the default; `loop_text` wins when both are given, else
`cta_text`, else `DEFAULT_LOOP_TEXT`.

`DEFAULT_CTA_TEXT` stays exported as a **deprecated alias** so downstream imports
don't break. Its *value* is deliberately left unchanged (the old `"Follow for
more."`) rather than aliased to `DEFAULT_LOOP_TEXT`: re-pointing it would silently
change the constant's value for any code still importing it — a minimal-surprise
violation (CLAUDE.md §9.5). New code should use `DEFAULT_LOOP_TEXT`.

### 4. A length **band** — add a floor to the existing ceiling; flag, never pad

`ShortScript` gains a `below_shorts_floor: bool` field, and `ScriptBuilder` gains a
`shorts_floor_ms` knob + a `SHORTS_FLOOR_MS` module constant (default `45_000`, the
QC-rubric minimum for a retention-viable short). `below_shorts_floor` is
`total_estimated_ms < floor` (strictly under — at-or-above the floor is in-band).
The existing `exceeds_shorts_ceiling` + `target_duration_ms = min(total, ceiling)`
behavior is unchanged.

This preserves the ADR 0038 **flag-don't-scale** philosophy in both directions: the
per-beat estimates stay honest WPM numbers; over-length is surfaced (never
truncated) and under-length is surfaced (never fabricated up to length — padding a
thin script with filler would be dishonest and would not improve retention). The
constructor validates `shorts_floor_ms > 0` like the other knobs, and additionally
guards `floor ≤ ceiling` (a floor above the ceiling is an empty band and a caller
error).

### 5. Package exports

`app/scripting/__init__.py` exports `DEFAULT_LOOP_TEXT` and `SHORTS_FLOOR_MS`
alongside the existing symbols; the new `BeatRole` members are reachable via
`BeatRole`. The deprecated `DEFAULT_CTA_TEXT` alias stays exported.

## Consequences

**Positive.** The script now carries an explicit, machine-checkable retention arc
(a distinct payoff position and a loop beat) and a two-sided length band — the #134
DoD structural asks. The `MediaPipeline` consumes `beats` generically and does not
switch on role, so the downstream media path is untouched. Old persisted scripts
still deserialize (deprecated values retained). Existing callers passing `cta_text=`
keep working (aliased). Pure, hermetic, no new dependency.

**Negative / deferred.** This step delivers *labelling*, not *writing*. A payoff
labelled in the final position is only as sharp as the prose the strategist authored;
a LOOP beat only actually loops if its copy seams back — both are the writing model's
job, a separate future step. The `BODY`/`CTA` deprecated values linger on the enum
until a future migration retires persisted records that use them.

**Risks.** Low and bounded. The change is contained to `app/scripting/` + its tests
(confirmed by grep: no role switch exists outside the package, and `MediaPipeline`
does not branch on `BeatRole`). The arc mapping, the band flags at their boundaries,
the `cta_text→loop_text` alias precedence, and the deprecated-role round-trip are all
covered by hermetic unit tests.

## Alternatives considered

- **Remove `BODY`/`CTA` from the enum.** Rejected: `StrEnum` values are serialized
  in persisted `ShortScript`s; deletion breaks deserialization of old records for no
  gain. Deprecate-in-place is the backward-compatible choice (CLAUDE.md §9.2).

- **Re-point `DEFAULT_CTA_TEXT` at `DEFAULT_LOOP_TEXT`.** Rejected: it silently
  changes the constant's value for any code still importing it (minimal-surprise
  violation, §9.5). Kept at its old value as a pure deprecated alias.

- **Pad/scale a below-floor script up to the floor.** Rejected: fabricating filler
  is dishonest and does not improve retention; the honest move is the same
  flag-don't-scale posture ADR 0038 chose for the ceiling, applied to the floor.

- **A dedicated recap/summary beat before the LOOP.** Rejected as speculative
  overbuild (CLAUDE.md §7): the arc's four positions are the DoD ask; a fifth beat
  has no consumer yet and can be added by a future ADR if the rubric grows.

- **Infer BUILD vs. PAYOFF from content (e.g. sentiment / keyword).** Rejected:
  that is judgment (§4) and belongs to the writing model, not a deterministic tool.
  Positional labelling (last line = payoff) is the honest structural rule.
