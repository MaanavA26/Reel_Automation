# ADR 0032: End-to-End Video Pipeline — the topic → finished video linchpin

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

By this point the two big subsystems are individually complete and tested:

- the **Deep Research engine** (`app/workflows/deep_research.py` — `run_research`)
  turns a topic into a `ResearchState` whose Publishing band carries a
  `CreatorPacket` (§5.4 handoff artifact); and
- the **Media Production layer** (`app/media/pipeline.py` — `MediaPipeline`,
  ADR 0025) turns a `CreatorPacket` into a `MediaPlan` (assembled-video
  descriptor) by chaining the TTS / subtitle / composition seams, with real
  adapters behind each seam (ADR 0022 TTS, ADR 0023 ffmpeg, ADR 0024 stock
  visuals).

What did **not** exist was the single path that runs them back-to-back, and the
composition root that wires *real* providers for it. Two honest holes blocked a
live run:

1. **`build_research_deps` raised.** The composition root had no production
   `SearchProvider` or model provider wired — it deliberately raised
   `CompositionError` rather than ship a `Fake*`. So `/research` always 503'd
   even with keys configured.
2. **No topic → video orchestrator.** Nothing chained `run_research` → packet →
   `MediaPipeline` → a finished artifact, and nothing surfaced it (no CLI, no
   API).

This ADR fills both — the **linchpin** the whole project exists to deliver
(CLAUDE.md §1/§3).

## Decision

### 1. Wire real providers in the composition root (it stops raising)

`app/services/composition.py` now selects concrete providers by configuration
(CLAUDE.md §6, policy-driven, provider-neutral):

- **Model.** `_build_model_provider` dispatches on `default_provider`:
  `openai-compatible` (base_url + api_key), `gemini` (native structured output,
  ADR 0020), or any named preset in `app.services.llm.providers`
  (`groq`/`nvidia`/`huggingface`/`ollama`, ADR 0028 — *reused*, not
  re-implemented). The router registers the provider under the **config name**
  (`default_provider`), not the adapter's own `.name`, because the default
  policy keys every role by `default_provider` and a registry preset's adapter
  is always named `"openai-compatible"`; registering under the config name is
  what makes role resolution succeed for every selectable backend.
- **Search.** `_build_search_provider` dispatches on a new `search_provider`
  setting: `tavily` (ADR 0013) or `brave` (ADR 0021), each reading its own key.

A missing key or unknown name surfaces as a loud `CompositionError` (mapped to
503 at the API seam) — never a silent `Fake*` in a running service. This keeps
the original "fail loud at the seam" stance while finally making a configured
key *work*.

### 2. `VideoPipeline` service (`app/services/video/`)

A deterministic **service** (CLAUDE.md §4 — orchestration of existing
tools/agents, no new judgment): `VideoPipeline.create(topic)` runs
`run_research` → guards the handoff → optionally retrieves B-roll → `MediaPipeline`
→ projects a `VideoArtifact` (the finished video's uri + metadata + the re-join
ids that trace it back through the chain). All collaborators are injected
(`ResearchDeps`, a new `MediaDeps` bundle), so it is fully hermetic with fakes
and config-gated for a live render (`build_video_pipeline`).

**The load-bearing logic is the handoff guard.** A research run can terminate
`FAILED` (no packet) or `COMPLETED` (including the revision-exhausted best-effort
path). Only a `COMPLETED` run with a published, *narratable* packet is
renderable; anything else raises `VideoPipelineError` rather than indexing into
an empty `packets` list.

`MediaDeps` mirrors `ResearchDeps`: a typed bundle of the media seams. The
`SubtitleService` is deliberately *not* in it — it is pure shipping code
`MediaPipeline` defaults to internally. `build_media_deps` wires the live seams:
`HttpTtsProvider` with a filesystem audio sink returning a `file://` uri the
ffmpeg adapter can resolve; `FfmpegCompositionService`; and, gated on the stock
key, `StockVisualProvider` **plus a `VisualSink`**.

**The `VisualSink` is load-bearing for a live render** (and a correction to an
early design): `StockVisualProvider` mints *remote* `https` B-roll uris, but
`FfmpegCompositionService.resolve_local_path` accepts only `file://`/bare paths —
so a remote uri would raise. The injected sink (the visual analogue of the TTS
`AudioSink`) fetches each remote uri to a local file under `media_output_dir` and
returns its `file://` uri; `VideoPipeline` maps the retrieved uris through it
(off the event loop) before composition. Without it there is no key combination
under which a live render produces a file: no stock key → empty visuals →
ffmpeg's "≥1 visual required"; stock key but raw https → `resolve_local_path`
raises. The sink closes that gap entirely in owned files (Media internals
untouched).

### 3. Surfaces (thin, logic in the service — CLAUDE.md §10)

- **CLI:** `python -m app.cli.make_video "<topic>"` (mirrors `app.cli.plan`).
- **API:** `POST /api/v1/videos` (synchronous, returns the `VideoArtifact`) plus
  an async `POST /api/v1/videos/jobs` (202 + `VideoJob`) and
  `GET /api/v1/videos/jobs/{id}`, backed by a `VideoJobStore` — the video-band
  analogue of the research `JobStore` (ADR 0031), same single-process,
  non-durable, `asyncio.Lock`-guarded model.

## Consequences

### Positive

- A configured key now makes `/research` **and** the new video path actually run
  — the project's first end-to-end deliverable.
- The full topic → video-artifact path is proven **hermetically** with the repo's
  Fake providers (no network, no LLM, no ffmpeg, no PyPI).
- Additive: Deep Research and Media internals are untouched; the model
  `factory.py` is untouched (provider dispatch lives in the owned composition
  root). The change is concentrated in the wiring layer.

### Negative / deferred

- **The live render needs the `ffmpeg` binary, network, and a visual source.**
  Real ffmpeg requires ≥1 visual and `file://`/bare-path assets, so
  `build_media_deps` wires a stock provider + a `VisualSink` (gated on
  `stock_api_key`) and a filesystem TTS sink. With no stock key the live render
  fails loud in ffmpeg (the honest behavior) — the hermetic fake path is
  unaffected (it tolerates empty visuals + `fake://` uris and wires no sink). A
  live smoke test is deferred (network + binary gated), consistent with the other
  `@pytest.mark.integration` adapters; the sink + provider selection are
  construction-tested hermetically.
- **The video job store is in-memory, single-process, non-durable** (ADR 0031's
  standing limitation, inherited). Streaming progress and `CANCELLED` stay
  deferred.
- No visual ranking/judgment: the pipeline queries B-roll by the narrative title
  and forwards the uris in order — `what` to depict was upstream strategist
  judgment; this service only executes (CLAUDE.md §4).

## Alternatives considered

- **Put model-provider dispatch in `factory.py`.** Rejected: `factory.py` is
  out of scope for this work and the dispatch is wiring, which belongs in the
  composition root. Reusing `providers.build_provider` keeps the registry the
  single owner of preset URLs.
- **Model the video orchestrator as an agent.** Rejected (CLAUDE.md §4): the
  orchestration is deterministic sequencing; every judgment already happened in
  the injected research agents and media strategist.
- **A bespoke video job envelope merged into `ResearchState`.** Rejected: a
  finished video has no all-in-one state object (a `QUEUED` job has no artifact
  yet), so a small typed `VideoJob` envelope is clearer than overloading the
  research state.
