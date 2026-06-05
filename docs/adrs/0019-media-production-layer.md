# ADR 0019: Media Production Layer — Provider-Neutral Seams

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

CLAUDE.md §3.3 names a **Media Production Layer** — the pipeline that turns
creator-ready research into a finished vertical short-form video: TTS, subtitle
generation, image/video generation or retrieval, composition, FFmpeg-based
assembly, and export. CLAUDE.md §3.4/§16 require a new major layer to be
introduced via an ADR; this is that ADR.

The Deep Research layer (`app/agents`, `app/workflows`, `app/services`,
`app/schemas`) is mid-build (M1–M11). The media layer is a **distinct, later**
component. Per the component-first philosophy (§2) and the "no speculative
overbuild / controlled bounded progress" rules (§7/§13), the right move now is
to establish the layer's **seams** — interfaces, typed artifacts, fakes, and the
one piece of genuinely deterministic real logic — not to build a media pipeline
nobody can yet feed.

The build sandbox also has no media vendors and no bundled `ffmpeg`/TTS SDKs,
the same network/binary constraint that batched the LLM and search adapters into
M-LP (ADR 0003/0006). So real adapters defer regardless.

## Decision

**Introduce `backend/app/media/` as a provider-neutral, fully-faked scaffold of
deterministic tools — no agents, no real binaries — mirroring the Deep Research
service-fabric pattern.**

### 1. The whole layer is *tools*, never agents (CLAUDE.md §4)

§4 explicitly lists "subtitle generation," "composition," "FFmpeg wrappers," and
"rendering" as tool/service work. None of TTS, subtitles, or composition
requires judgment — they are deterministic execution. So the media layer
contains **services with no `ModelProvider`/router dependency**. (Judgment about
*what* to narrate — script/hook/angle selection — already lives upstream in the
Deep Research Short-Form Content Strategist, §5.6.)

### 2. Three seams, mirroring the twice-blessed fabric pattern

Each capability is a provider-neutral contract so concrete adapters
(ElevenLabs/Azure for TTS, Veo/stock for visuals, real `ffmpeg` for
composition) are swappable behind the interface (ADR 0003/0006):

- **`tts/base.py`** — `TTSProvider` Protocol (`async synthesize(text, voice) ->
  SynthesizedSpeech`) + hermetic `FakeTTSProvider`. Async (real TTS is network
  I/O).
- **`composition/base.py`** — `CompositionService` Protocol (`async render(audio,
  captions, visual_uris, …) -> RenderedVideo`) wrapping the future FFmpeg step +
  hermetic `FakeCompositionService`. Async (real render = subprocess + storage
  I/O). **No `ffmpeg` is invoked.**
- **`subtitles/base.py`** — deliberately **asymmetric**: this band's real path
  needs no external service, so it ships real code now — a **synchronous**
  `SubtitleService` Protocol, a concrete `DeterministicSubtitleService` (zips
  segments + timings → `CaptionTrack`), and pure stdlib `format_srt` /
  `format_vtt` formatters. There is no `FakeSubtitleService`; faking pure
  deterministic code would be pointless (testing-standards: "don't mock what you
  can fake — and don't fake what is already pure"). Sync because the path is
  CPU-only; an async forced-alignment variant is deferred.

### 3. Typed artifact DTOs with required provenance (`app/media/schemas.py`)

`SynthesizedSpeech` (`aud_`), `CaptionTrack`/`Caption` (`sub_`), `RenderedVideo`
(`vid_`) — all `extra='forbid'`, id-prefixed, matching the repo's `_gen_id`
scheme. Each top-level artifact carries a **typed, required `produced_via`**
string (`"tts:fake"`, `"subtitles:deterministic"`, `"composition:ffmpeg"`, …),
symmetric with `Source.discovered_via` / `Evidence.extracted_via` (ADR 0006).
Media artifacts are the same pipeline's tail; provenance integrity (CLAUDE.md
§11) does not stop at the research boundary.

Caption times are **integer milliseconds** (`start_ms`/`end_ms`), not float
seconds, so the ms→`HH:MM:SS,mmm` conversion is exact and rounding-free.

### 4. The layer stands alone — no import of the Deep Research schema

`app/media/` imports **nothing** from `app.schemas` / `app.agents` /
`app.workflows`. `_gen_id` is a small **local copy** (not an import) of the
`research_state` helper. This decoupling is deliberate: it keeps the layer
independently buildable/testable/showcaseable, and it coheres with the deferred
handoff contract (below) — we are not yet committing to a coupling whose shape
we will only know once both layers exist.

## Consequences

### Positive

- The layer's architecture is established and showcaseable now (clean seams,
  full types, hermetic tests) without speculative feature code (§7/§12).
- The provider-abstraction makes Veo/ElevenLabs/real-ffmpeg drop-in adapters,
  reusing the exact M2/M5/M6 fabric pattern — twice-blessed, low surprise.
- `produced_via` extends the provenance discipline end-to-end; an exported video
  records which tool made each of its parts.
- The SRT/VTT formatters are real, pure, fully unit-tested value today.

### Negative

- The seams (TTS, composition) are fake-backed: they verify the *contract and
  wiring*, not real synthesis/render quality — the same offline ceiling the
  research fabric has (ADR 0006). Quality is a future network-gated concern.
- A second `_gen_id` copy now exists. Accepted as the cost of layer decoupling;
  the shared convention (prefix + 64 bits hex) keeps them aligned by documented
  contract, not by a cross-layer private import.

### Neutral

- `Recorded*` call-capture dataclasses on the fakes mirror
  `FakeSearchProvider.RecordedSearch`.
- No new runtime dependency is added (stdlib + Pydantic only).

## Deferred (with reasons)

- **Real adapters** — `TTSProvider` (ElevenLabs/Azure), `CompositionService`
  (`ffmpeg` via subprocess), and image/video generation-or-retrieval (Veo/stock)
  → a network/binary-gated milestone behind these protocols, with
  `@pytest.mark.integration` smoke tests. The sandbox lacks the vendors/binaries
  (the ADR 0003/0006 deferral).
- **The Deep Research → Media handoff contract** — how the creator packet
  (§5.4, M12) maps to media inputs (script segments → narration text + caption
  timings → composition asset bundle). This is the cross-layer seam and earns
  its **own** ADR once M12's creator-packet shape is fixed; binding it now would
  couple the layer to a not-yet-final schema.
- **Image/video generation-or-retrieval and PPT generation** (§3.3) — not yet
  seamed; added when a composition consumer needs them, to avoid speculative
  surface (§7).
- **A media orchestrator / pipeline graph** chaining TTS → subtitles →
  composition — added when there are real artifacts to chain end-to-end.
- **Async forced-alignment** subtitle variant (refine timings against real
  audio) — needs a real TTS adapter to align against.

## Alternatives considered

### Option A — Defer the whole layer until Deep Research finishes

**Pros:** maximal focus on the in-flight component. **Cons:** §3.4/§16 want the
layer introduced via ADR, and the seams are genuinely buildable and valuable
offline (mirroring how M2/M5 shipped fabric + fake before adapters). **Rejected:**
seaming now is bounded, not speculative, and de-risks the eventual handoff.

### Option B — Model media steps as agents

A "Narration Agent," "Composition Agent," etc. **Pros:** superficially uniform
with the research layer. **Cons:** directly violates §4 — these are deterministic
execution, not judgment; it would be the §11 "make every step an agent"
anti-pattern. **Rejected.**

### Option C — Build the creator-packet → media handoff now

**Pros:** an end-to-end story. **Cons:** M12's creator-packet schema is not final;
coupling the layer to it now risks rework and contradicts decoupling. **Rejected:**
the handoff earns its own ADR when both sides exist.

### Option D — Reuse `research_state._gen_id` / put DTOs in `app/schemas`

**Pros:** no duplicated helper. **Cons:** couples the media layer to the Deep
Research schema module, undermining independent buildability and the deferred-
handoff stance; importing a private `_` symbol across layers is poor hygiene.
**Rejected** in favor of a documented local copy.

## References

- [CLAUDE.md](../../CLAUDE.md) §3.3 (Media Production Layer), §3.4/§16 (new layer
  via ADR), §4 (agent-vs-tool), §7/§13 (no overbuild / bounded progress), §11
  (provenance; no "every step an agent"), §12 (showcase).
- Related: [ADR 0003](0003-model-router-llm-fabric.md) (fabric + fake, adapter
  deferred), [ADR 0006](0006-source-discovery-and-search-fabric.md) (provider-
  neutral protocol + hermetic fake; typed required `discovered_via` provenance
  precedent), [ADR 0001](0001-research-state-and-provenance.md) (`_gen_id` /
  id-prefix / strict-model scheme this layer mirrors).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer section.
