# ADR 0025: Media Pipeline — the Deep Research → Media Handoff

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 established the Media Production Layer's seams (`TTSProvider`,
`SubtitleService`, `CompositionService` + DTOs + fakes) and **explicitly
deferred** two things: the concrete vendor/ffmpeg adapters (network/binary
gated) and *"the Deep Research → Media handoff contract — how the creator packet
(§5.4, M12) maps to media inputs … This is the cross-layer seam and earns its
own ADR once M12's creator-packet shape is fixed."*

M12's `CreatorPacket` shape is now fixed (`app/schemas/research_state.py`:
`hooks` / `angles` / `narratives` / `key_facts` / `warnings`). This ADR is the
deferred handoff ADR: it introduces the **one** piece that chains the seams
end-to-end into an assembled-video descriptor.

## Decision

**Add `backend/app/media/pipeline.py` — a deterministic `MediaPipeline` *tool*
(no LLM) that turns a `CreatorPacket` into a `MediaPlan` by chaining the three
existing seams.** Nothing else in the media layer changes; the real adapters and
visual sourcing remain deferred (ADR 0019).

### 1. It is a tool, not an agent (CLAUDE.md §4)

The judgment about *what* to narrate already happened upstream in the Short-Form
Content Strategist (`CreatorPacketAgent`, M12) — the packet's `narratives` are
its output. The pipeline only *executes* deterministic assembly: select →
synthesize → time → compose. A scoring/ranking step to pick the "best" narrative
would be judgment and therefore agent territory, so selection is deterministic
(`narratives[narrative_index]`, default 0).

### 2. The intentional ADR 0019 §4 coupling exception

ADR 0019 §4 holds that `app/media/` imports **nothing** from `app.schemas`. This
module is the **single, deliberate exception**: `pipeline.py` imports
`CreatorPacket` because it *is* the cross-layer handoff seam ADR 0019 deferred.
The exception is contained to this one file — `tts`, `subtitles`, `composition`,
and `schemas` stay decoupled and independently buildable. Recording the
exception explicitly (rather than silently breaking the invariant) is the point
of this ADR.

### 3. The handoff contract

- **Input:** a `CreatorPacket` + an optional `narrative_index` and pass-through
  `visual_uris`.
- **Mapping:** the chosen `NarrativeOption.script_outline` is split line-wise
  into non-blank **beats** (one caption cue per beat). Blank beats are skipped
  and logged; if no beat survives — or the packet has no narrative at the index
  — `MediaPipelineError` is raised. This mirrors `IngestionService` exactly:
  *tolerate per-item failures, raise only when nothing results.*
- **Output:** a `MediaPlan` (defined in `pipeline.py`, not `schemas.py`, to keep
  the seam DTOs untouched) bundling the chosen narrative, the three produced
  artifacts (`SynthesizedSpeech` / `CaptionTrack` / `RenderedVideo`), and
  `source_packet_id` for re-join (symmetric with `CreatorPacket.report_id` /
  `KeyFact.finding_id`). It carries its own required `produced_via`
  (`"media:pipeline"`), extending the layer's provenance discipline.

### 4. The timing invariant (the one correctness anchor)

`CompositionService.render` takes a **single** `SynthesizedSpeech`, so narration
is synthesized **once** over the joined script — per-segment synthesis has
nowhere to go. Caption timings are then allocated across beats by **cumulative
integer boundaries** proportional to each beat's character length:

```
boundary_i = round(total_ms * cumsum_len_i / total_len)
start_i, end_i = boundary_{i-1}, boundary_i
```

This guarantees, with no rounding drift / gaps / overlaps:

```
track.cues[-1].end_ms == audio.duration_ms == video.duration_ms
```

A dedicated test asserts this invariant (incl. an awkward total that does not
divide evenly).

### 5. DI mirrors `IngestionService`

Constructor injection: `tts_provider` and `composition_service` are **required**
(their reals are deferred — ADR 0019); `subtitle_service` **defaults to the real
`DeterministicSubtitleService`** (pure, hermetic shipping code — exactly as
ingestion defaults `pdf_parser` to the real `PypdfParser`). A `voice="narrator"`
default rounds it out. The main method is `async` (TTS + composition are async).

## Consequences

### Positive

- The deferred ADR 0019 handoff exists and is end-to-end runnable + hermetic.
- The timing invariant makes the audio/caption/video durations provably aligned.
- `produced_via` end-to-end now reaches the assembled video descriptor.

### Negative

- One file (`pipeline.py`) is now coupled to the Deep Research schema. Accepted
  and contained: it is the handoff seam by definition; the rest of the layer
  stays decoupled.
- The seams are still fake-backed — the contract/wiring is verified, not real
  synthesis/render quality (the ADR 0019 offline ceiling).

### Neutral

- No new dependency (stdlib + Pydantic). `MediaPipeline` is not yet wired into
  any graph/API — it is a standalone tool, consistent with the layer's posture.

## Deferred (with reasons)

- **Real adapters** (ElevenLabs/Azure TTS, real ffmpeg composition, Veo/stock
  visuals) — unchanged from ADR 0019; the pipeline is adapter-agnostic behind
  the protocols.
- **Visual sourcing** — `visual_uris` is a pass-through; no visual provider is
  invented (ADR 0019 deferral). An empty list renders narration + captions only.
- **Multi-narrative / A-B render plans, hook/angle composition, SRT/VTT export
  of the chosen track** — bounded follow-ons once a consumer needs them (§7).
- **Wiring into a media orchestration graph / API surface** — added when there
  is a caller; the tool is independently usable today.

## Alternatives considered

### Option A — Per-segment TTS, one audio per beat
**Rejected:** `CompositionService.render` takes a single audio; N audios have
nowhere to go without changing the (untouchable) seam. Synthesize-once +
proportional timing is the contract the seam actually affords.

### Option B — Put `MediaPlan` in `app/media/schemas.py`
**Rejected:** the task scope is `pipeline.py` + tests only; editing the seam DTO
module risks the "don't touch existing media" boundary. `MediaPlan` lives with
the orchestrator that produces it.

### Option C — A structural Protocol input instead of importing `CreatorPacket`
**Rejected:** the handoff's purpose is to bind to the real packet shape now that
M12 is fixed; a structural dodge would hide the coupling this ADR exists to own.

## References

- [ADR 0019](0019-media-production-layer.md) — the seams + DTOs + fakes; the
  deferred handoff this ADR fulfills (its §4 decoupling invariant + the
  "Deferred / Option C" handoff note).
- [ADR 0018](0018-creator-packet.md) — the `CreatorPacket` shape consumed here.
- `app/services/ingestion/service.py` — the injected-provider DI + skip/raise
  contract mirrored.
- [CLAUDE.md](../../CLAUDE.md) §3.3 (Media layer), §4 (tool-vs-agent), §5.4
  (creator packet), §7 (no overbuild), §11 (provenance).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer section.
