# ADR 0038: Short-Form Script + Shot-List Builder

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** 0061 (the hook → body → CTA script shape; the tool /
  shot-list design otherwise stands)

## Context

The Deep Research engine produces a `CreatorPacket` (M12, ADR 0018): hooks,
content angles, short narrative options, key facts, and code-derived
unsafe/unverified-claim warnings. The Media Production layer's `MediaPipeline`
(ADR 0025) already consumes the packet directly — it picks a `NarrativeOption`,
splits its `script_outline` line-wise, synthesizes narration, and times
captions.

What is missing between them is a **retention-optimized, beat-structured script**
the media pipeline (and future media agents) can plan against: a single chosen
hook + narrative composed into an ordered hook → body → CTA arc, with per-beat
duration estimates and per-beat visual cues for the B-roll retrieval seam
(`VisualProvider`, ADR 0024), bounded to the ≤60s Shorts envelope. Today the
pipeline derives only body beats and invents no hook beat, no CTA, no visual
cue, no duration budget.

## Decision

**Add a new `backend/app/scripting/` package — a deterministic `ScriptBuilder`
*tool* (no LLM) that turns a `CreatorPacket` into a typed `ShortScript`.** It is
upstream of `MediaPipeline` and standalone; no existing file changes (the package
is the sole writer's surface).

### 1. It is a tool, not an agent (CLAUDE.md §4 — the borderline case named)

Composing a script from a packet is the explicit §4 borderline. The split:
the *creative wording* (hook phrasing, the narrative arc) is judgment and
**already happened upstream** in the Short-Form Content Strategist
(`CreatorPacketAgent`). This builder does only the **deterministic structuring**:
select → split into ordered beats → estimate durations → derive visual cues →
append a claim-free CTA. There is no LLM call and no judgment about *what to
say*. Creative LLM rewriting of the structured beats is a **documented future
enhancement**, not v1 (see "Deferred").

### 2. The `ShortScript` shape

- `ShortScript` (id-prefixed `scr_`, strict): `source_packet_id` (re-join,
  mirroring `MediaPlan.source_packet_id`), `narrative_title`, ordered `beats`,
  the advisory timing triple, carried-forward `warnings`, and `built_via`.
- `ScriptBeat` (id-less sub-unit, mirroring `HookIdea`): `role`
  (HOOK/BODY/CTA), `text` (a single clean line — no internal newlines, so one
  beat maps 1:1 onto a `MediaPipeline` narration segment / caption cue),
  `estimated_duration_ms`, `visual_keyword`, plus the §11 fields `disputed` and
  `finding_ids`.
- Order is fixed by construction: exactly one HOOK, one BODY per non-blank
  `script_outline` line, exactly one CTA.

### 3. §11 honesty — flag, never smooth

A beat's `finding_ids` and `disputed` flag are **code-derived** from the
packet's `KeyFact` map (`finding_id → disputed`). The hook is flagged by its own
`HookIdea.finding_ids`; every body beat is flagged by the narrative's whole-arc
`finding_ids`. There is **no per-line attribution** in the packet, so the builder
flags at the element level (honest that *the arc rests on* a disputed finding)
without fabricating per-line precision. The relevant `CreatorWarning`s — those
sharing a `finding_id` with a used beat (ADR 0018's shared-`finding_ids`
cross-reference) — are carried forward **verbatim** onto the `ShortScript`, the
non-omittable §11 posture one layer downstream. So a disputed claim is *flagged
on the beat and kept as a warning*, never silently polished away.

Why filter warnings to the selected arc rather than carry the packet's full set:
the `ShortScript` is a **single-narrative selection** (one hook + one narrative),
so a warning about a finding no beat uses is about content this script does not
contain — carrying it would be noise, not honesty. Non-omittability is preserved
because a *used* disputed finding is flagged on its beat **regardless** of whether
a warning exists (the flag derives from the always-present `KeyFact` map, not from
the warnings), so filtering can never let a disputed-but-used claim slip through.

### 4. The CTA is claim-free structural scaffolding

The CTA beat is required by the hook→body→CTA shape but is **topic-neutral
scaffolding** (default `"Follow for more."`, caller-overridable). It asserts
nothing about the topic, so it is structurally **exempt** from grounding: always
empty `finding_ids`, always `disputed=False` — even when every topical finding
is disputed. This keeps "only use packet content" intact: the §11 rule governs
*claims*, and the CTA makes none.

### 5. Timing — flag overflow, do not scale or raise

Per-beat `estimated_duration_ms` is an honest words-per-minute estimate
(~150 wpm, a constructor knob). The script's timing triple:

- `total_estimated_ms` — the honest **sum** of the beat estimates.
- `target_duration_ms` — `min(total_estimated_ms, SHORTS_CEILING_MS)` (60s).
- `exceeds_shorts_ceiling` — True iff the sum exceeds 60s.

When the script overruns 60s the builder **flags** it rather than (a) scaling
beats to force-fit — which would make the estimates dishonest — or (b) raising,
which would reject a fixable script. The number is **advisory**: `MediaPipeline`
does the real timing post-TTS over actual audio duration (ADR 0025), so the
estimate need only be stable, and overflow is a signal for the caller / a future
editorial loop, not a hard failure.

### 6. Visual keyword — a deterministic seed, not retrieval

`visual_keyword` is a deterministic stopword-strip → top-3-content-tokens
(order preserved, lowercased) with safe fallbacks. It is only a **seed** for the
`VisualProvider` retrieval seam (ADR 0024), which owns retrieval quality. Kept
deliberately simple; LLM keyword refinement is a documented future enhancement.

### 7. Selection + errors mirror `MediaPipeline`

Deterministic `hook_index` / `narrative_index` (default 0; ranking would be
judgment, §4). `ScriptBuilderError` (mirroring `MediaPipelineError`) is raised on
missing/out-of-range hook or narrative, or a narrative with no narratable beat —
never an empty or hook-only script.

## Consequences

### Positive

- A typed, retention-shaped script artifact now sits cleanly between the
  creator packet and the media pipeline; pure + fully unit-testable.
- The §11 disputed-claim honesty is carried structurally into the script
  surface (per-beat flag + non-omittable warnings).
- Beat shape is **compatible by construction** with `MediaPipeline` (one beat =
  one line = one segment = one cue), so future wiring is a trivial change.

### Negative

- The builder is not yet wired into a media orchestration graph/API — it is a
  standalone tool, consistent with the layer's posture (a consumer wires it).
- Disputed flagging is element-level, not per-line (the packet affords no finer
  attribution). Accepted as the honest ceiling of the available data.

### Neutral

- No new dependency (stdlib `re` + Pydantic). No change to `config.py`,
  `main.py`, `pyproject.toml`, `app/media/`, or `schemas.py` — the package's
  only schema coupling is a read-only import of `CreatorPacket` / `CreatorWarning`
  / `HookIdea` / `NarrativeOption`, and a copied `_gen_id` (the ADR 0019-blessed
  copy-not-import move).

## Deferred (with reasons)

- **LLM creative rewriting of beats** — the documented v2: an agent that
  rewrites the structured beats for punchier voiceover. v1 is deterministic
  structuring so the honest baseline exists and is testable first.
- **`key_facts` as supplementary body beats** — v1 uses `key_facts` only as the
  disputed/grounding map; the beat structure is narrative-driven. A fact-insertion
  pass is a bounded follow-on once a consumer needs it.
- **Per-line finding attribution / multi-narrative A-B scripts / angle-driven
  variants** — bounded follow-ons once the packet carries finer attribution or a
  consumer needs alternatives.
- **Wiring into a media orchestration graph / API** — added when there is a
  caller; the tool is independently usable today (mirrors `MediaPipeline`).

## Alternatives considered

### Option A — Scale beat durations to force-fit 60s
**Rejected:** it makes `estimated_duration_ms` dishonest (no longer the WPM
estimate). Flagging overflow keeps every per-beat number truthful and defers the
real timing to `MediaPipeline`'s post-TTS allocation.

### Option B — Raise when the script exceeds 60s
**Rejected:** overflow is fixable (trim the narrative) and the estimate is
advisory, so a hard failure is too strict. Flag and let the caller decide.

### Option C — Put the script DTOs in `app/media/schemas.py` or `app/schemas/`
**Rejected:** scope is the new `app/scripting/` package only; the script is a
scripting-band artifact and lives with the tool that produces it (the same
placement rationale as `MediaPlan` in `pipeline.py`, ADR 0025 Option B).

### Option D — Make it an agent / add an LLM rewrite in v1
**Rejected:** structuring is deterministic and procedural (§4 → tool). The
creative judgment already happened in M12; an LLM pass is a documented v2, kept
out of the honest deterministic baseline.

## References

- [ADR 0025](0025-media-pipeline.md) — the `MediaPipeline` that consumes the
  packet; the selection/skip/raise + line-split contract mirrored here.
- [ADR 0018](0018-creator-packet.md) — the `CreatorPacket` shape consumed, and
  the shared-`finding_ids` warning cross-reference carried forward.
- [ADR 0024](0024-visual-retrieval.md) — the `VisualProvider` B-roll seam the
  `visual_keyword` seeds.
- [ADR 0019](0019-media-production-layer.md) — the copy-not-import `_gen_id`
  convention reused.
- [CLAUDE.md](../../CLAUDE.md) §4 (tool-vs-agent, the borderline named), §5.4
  (creator packet), §7 (no overbuild), §9 (scope), §11 (provenance / honesty).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer section.
