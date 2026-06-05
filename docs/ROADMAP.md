# Deep Research Engine — Execution Roadmap

> **Status doc for the autonomous build loop.** This is the resumable source of
> truth for "where are we and what's next." It is derived from `CLAUDE.md`
> (the four Deep Research bands + named agents) — it does **not** re-decide the
> architecture, it sequences it into bounded, PR-sized milestones.
>
> **Operating model:** one reviewable PR per milestone; tests + ADR (for
> architectural decisions) per the engineering standards; strict agent-vs-tool
> separation. Continuous progress — no approval pause between milestones.

## Legend
- ✅ done (merged) · 🔨 in progress · ⬜ not started

## Phase 0 — Schema foundations (✅ complete)
- ✅ `ResearchState` + provenance schema (`Source`, `Chunk`, `Evidence`) — ADR 0001, PR #9
- ✅ Research Control band schema (`ResearchPlan`, `SubQuestion`) — PR #10

## Foundations — behavioral layer enablement
- ✅ **M1 — Workflow skeleton + node contract.** LangGraph graph wiring `ResearchState`
  through async stub nodes that compile and run end-to-end (queued → … → completed).
  Node I/O contract = partial-state-update returns; fan-out accumulation deferred to M5/M7.
  Adds `langgraph` dep. [ADR 0002](adrs/0002-langgraph-workflow-integration.md).
- ✅ **M2 — Model router / LLM fabric.** Provider-neutral, policy-driven role-based
  model selection (`services/llm/`): `ModelProvider` protocol, `ModelRouter`/`RolePolicy`,
  config-sourced `default_policy`, hermetic `FakeProvider`. Concrete Anthropic adapter
  deferred to M3 (its first consumer). [ADR 0003](adrs/0003-model-router-llm-fabric.md).
- ✅ **M2-Eval — LLM-as-judge eval harness.** `backend/app/eval/` — the reusable,
  offline-testable scaffold for "which model is best for role X" (CLAUDE.md §6),
  productizing the by-hand eval in [`docs/llm-model-selection.md`](llm-model-selection.md)
  (§6 "make it reproducible"). A deterministic *service*: `EvalHarness` runs explicit
  `(provider, model)` candidates across schema-bound `EvalTask`s, validates output, times
  via an injected clock, scores quality with a pluggable `Judge` (rule-based default +
  optional `ModelJudge` with a structural self-judge guard), and ranks via typed
  `EvalResult`/`EvalReport` (`best_choice()` → a `ModelChoice` for a policy). Hermetic via
  `FakeProvider`; live runner + CLI deferred. [ADR 0029](adrs/0029-llm-eval-harness.md).
  - ✅ **Response cache (fabric enhancement):** `CachingModelProvider` (`services/llm/cache.py`) —
    a decorator (composition) wrapping any `ModelProvider`, memoizing `complete_structured` by a
    stable SHA-256 over `(model, system, prompt, schema-identity)`. Pluggable `CacheBackend`
    protocol + stdlib in-memory default (optional `max_size` LRU); hit skips the wrapped call,
    miss populates; exceptions never cached; values deep-copied for isolation. Opt-in (trades
    freshness for cost on non-deterministic models); stdlib-only, no router/config change.
    Hermetic over `FakeProvider`. [ADR 0026](adrs/0026-llm-response-cache.md).

## Research Control band
- ✅ **M3 — Research Planner agent.** topic → `ResearchPlan` of `SubQuestion`s. First real node:
  `ResearchPlannerAgent` calls the router (`PLANNING` role), maps a model DTO into the schema
  (ids/timestamps schema-minted), and is wired into the `plan` node via factory-closure DI.
  Fully offline-verified via `FakeProvider`. [ADR 0004](adrs/0004-node-dependency-injection.md).
- 🔨 **M4 — Research Orchestrator (failure path done).** Deterministic failure path: `error` field,
  uniform exception→`FAILED` wrapper, first conditional edges (route off `status`) → terminal
  `failed` sink. [ADR 0005](adrs/0005-workflow-error-handling.md). **Remaining orchestrator work is
  distributed to its real consumers** (per ADR 0005 § Deferred): retries/budgets → M-LP (need live
  providers), progress → M13 (streaming API), `CANCELLED` → checkpointer milestone, quality
  gates/revision loops → M10 (Editorial Critic agent). The §5.6 "Orchestrator Agent" is aspirational
  until M10 gives it something to judge.
- ✅ **Budget guardrails (`services/budget/`).** Estimated-spend metering + **enforcement** so
  unattended runs can't overspend — the budgets half ADR 0005 deferred. A `BudgetTracker` accrues
  call counts + estimated cost per-run / per-calendar-day (injected `Clock`) and raises
  `BudgetExceededError` *before* a call breaches a ceiling (pre-call reservation; strict `>` boundary;
  both ceilings optional + independent; unmodeled model fails loud, never silent $0). Cost via a
  pluggable `CostEstimator` (`PerCallEstimator` / `TokenCostEstimator`) over a configurable
  `PriceTable`. A `BudgetedModelProvider` decorator estimates → reserves → blocks before the wrapped
  call, so a blocked call incurs no real spend. Estimate-vs-actual caveat documented (no token-usage
  in the provider contract → a conservative ceiling, not an invoice). Hermetic (`FakeProvider` + stub
  clock). **Capability only, no wiring** (M-LP pattern); the Orchestrator owns how to react to a block.
  [ADR 0035](adrs/0035-budget-guardrails.md).

## Knowledge Acquisition band
- ✅ **M5 — Source Discovery agent.** `SourceDiscoveryAgent` plans search queries via the
  model (`PLANNING` role, judgment) and retrieves `Source`s via an injected `SearchProvider`
  tool (`services/search/`, faked offline; real adapter → M-LP). The LLM never mints a
  `Source.url` (§11 evidence-vs-inference, enforced structurally); added typed
  `Source.discovered_via`. Single-node acquire keeps the fan-out reducer deferred to the
  checkpointer milestone. [ADR 0006](adrs/0006-source-discovery-and-search-fabric.md).
- ✅ **M6 — Source Ingestion (HTML v1).** Deterministic fetch + parse + chunk: a `FetchProvider`
  fabric (`services/ingestion/`) with a real hardened `HttpxFetchProvider` + `FakeFetchProvider`,
  a pure stdlib HTML parser + fixed-window chunker, and an `IngestionService` wired into a new
  `ingest` node (`plan→acquire→ingest→reason→publish`). WEB-only v1; PDF/YouTube/OCR (Azure DI,
  Nvidia) deferred to M-LP; `Chunk.parsed_via` deferred until a multi-parser exists.
  [ADR 0008](adrs/0008-source-ingestion-and-fetch-fabric.md).
- ✅ **M7 — Evidence Extraction agent.** chunks → `Evidence` (`EvidenceExtractionAgent`,
  `EXTRACTION` role). The model authors only `claim` + `confidence`; provenance
  (`source_id`/`source_url`/`chunk_id`/`chunk_text`) is **code-attached** from the real
  `Chunk`/`Source` — §11 evidence-vs-inference made structural (third agent to enforce it).
  Per-chunk isolation; tolerates per-chunk failures, raises on zero total. New `extract` node
  (`plan→acquire→ingest→extract→reason→publish`). Introduces the `ResearchDeps` container (the
  M6-flagged kwarg-threshold trigger); fan-out reducer + per-chunk concurrency stay deferred to
  the checkpointer milestone. [ADR 0009](adrs/0009-evidence-extraction.md).

## Knowledge Reasoning band
- ✅ **M8 — Cross-Verification agent.** Evidence → `Verdict`s (`CrossVerificationAgent`,
  `PLANNING` role). A deterministic stdlib **claim-blocking tool** (`services/reasoning/`)
  groups related claims into clusters (bounding the O(N²) cross-product); the agent judges each
  cluster. §11 made structural twice: the model references evidence only by local index
  (code resolves+validates ids), and `CORROBORATED` requires ≥2 **distinct sources** —
  code-counted, never model-trusted (intra-source repetition is downgraded). New
  `KnowledgeReasoningState` substate; `verify` node replaces the `reason` stub
  (`plan→acquire→ingest→extract→verify→publish`). Thin support is a valid result (not a
  failure); fan-out reducer/concurrency deferred to the checkpointer milestone.
  [ADR 0010](adrs/0010-cross-verification.md).
- ✅ **M9 — Synthesis agent.** Verdicts → plan-anchored `Finding`s (`SynthesisAgent`,
  `LONG_CONTEXT` role). A single model call over the already-reduced verdict set (pure agent,
  no tool); the model authors prose + local indices, code resolves/validates verdict + sub-question
  ids (two separate index spaces). §11 keystone: the grounding summary (`disputed`,
  `weakest_support`) is **code-derived** from the cited verdicts — the model gets no self-report
  field, so a finding can't overstate its grounding and the caveat is carried forward
  non-omittably. New `Synthesis` substate (`reasoning.synthesis.findings`); `synthesize` node
  between verify→publish (now `plan→acquire→ingest→extract→verify→synthesize→publish`). Narrative
  layer + map-reduce deferred. [ADR 0011](adrs/0011-synthesis.md).
- ✅ **M10a — Editorial Critic (assessment).** Synthesis → `Critique` (`EditorialCriticAgent`,
  `PLANNING` role). Agent/tool split: a deterministic `coverage` tool (`services/reasoning/`)
  computes which sub-questions are uncovered; the agent judges quality (redundancy, balance,
  clarity, overstated-vs-disputed prose). §11 keystone: coverage + the accept/revise `decision`
  are **code-derived** (REVISE iff uncovered OR any issue), model authors only issues (by local
  F#/S# index, code-validated) + rationale; a disputed finding alone is NOT a revise trigger. New
  `Critique` substate (`reasoning.critiques`); `critique` node closes the band
  (`…→synthesize→critique→publish`, still **linear** — `decision` recorded, not yet routed on).
  [ADR 0012](adrs/0012-editorial-critic.md).
- ✅ **M10b — Revision loop.** The bounded `critique→synthesize` back-edge (the graph's first
  cycle): top-level `revision_iteration` counter + `max_syntheses` cap, `_make_critique_router`
  (the router, not the agent, owns termination — model proposes revise, code decides), explicit
  `recursion_limit` backstop, mandatory critique feed-forward into re-synthesis (`prior_critique`
  on `SynthesisAgent`), exhausted-completes-not-fails. One additive top-level lifecycle scalar
  (no change to the M10a `Critique`/reasoning schema). [ADR 0012](adrs/0012-editorial-critic.md).

## Knowledge Publishing band
- ✅ **M11 — Report generation.** Reasoning output → a structured, source-grounded `Report`
  (`ReportAgent`, `LONG_CONTEXT` role) — title/abstract/sections (model prose) + a code-derived
  citation bibliography (walked `Finding→Verdict→Evidence→Source`, snapshotted for export) + a
  **code-derived, non-omittable caveats list**. Agent/tool split: prose is the agent;
  `services/publishing/` (citations, caveats) is deterministic. §11 keystone: caveats range over
  the **full** findings set (so an uncited disputed finding still surfaces) and the
  `UNRESOLVED_CRITIQUE` banner fires when the revision loop exhausted unsatisfied (fulfilling
  ADR 0012's promise). New `ResearchPublishingState`; dedicated `report` node
  (`…→critique→report→publish`); `publish` is now the lifecycle terminal. Creator-packet fields
  deferred to M12. Deterministic `render_markdown` / `render_html` renderers
  (`services/publishing/markdown.py`, `html.py`) now fulfil ADR 0017's deferred renderer — citations
  and caveats always render (the §11 non-omittability carried to the output surface), HTML escapes
  all text. [ADR 0017](adrs/0017-report-generation.md).
- ✅ **M12 — Creator packet + downstream handoff artifacts.** Report + findings → a short-form
  `CreatorPacket` (`CreatorPacketAgent`, the Short-Form Content Strategist, `LONG_CONTEXT` role) —
  hook ideas, content angles, short narrative options (model creative prose) + **code-derived key
  facts** + a **code-derived, non-omittable unsafe/unverified-claim warnings list**. Agent/tool
  split: creative prose is the agent; `services/publishing/warnings.py` is deterministic, reusing
  M11's `finding_caveat_kind` predicate (no drift). §11 keystone, mirrored one layer up: warnings
  range over the **full** findings set (so an uncited disputed finding still surfaces a warning),
  tied to a hook/angle/narrative by **shared `finding_ids`**; the model authors no facts or
  warnings. Single finding index space (the report is prose context, not a second index space).
  New `CreatorPacket`/`HookIdea`/`ContentAngle`/`NarrativeOption`/`KeyFact`/`CreatorWarning` schema
  + `publishing.packets`; dedicated `packet` node (`…→report→packet→publish`); 9th `ResearchDeps`
  field. A thin/heavily-warned packet is valid, not a failure. [ADR 0018](adrs/0018-creator-packet.md).

## Surface
- 🔨 **M13 — API + job submission + frontend wiring (sync submit done).** `POST /api/v1/research`:
  thin router awaits `run_research` and returns the canonical terminal `ResearchState` (the response
  *is* the result, so it covers submit + read result/status in one). Composition root
  (`build_research_deps`, FastAPI-agnostic) assembles `ResearchDeps` from `Settings`, failing loud
  (`CompositionError → 503`) on still-missing collaborators (search → M-LP.2). Fake-backed
  `TestClient` suite drives the real workflow hermetically via `app.dependency_overrides`.
  [ADR 0016](adrs/0016-research-api-surface.md). **Deferred:** background/async execution, streaming
  progress, id-addressable `GET /research/{id}` + job store, frontend wiring.
  - 🔨 **M13 (async slice):** async job store + status endpoints. `POST /api/v1/research/jobs`
    enqueues + returns **202** with the `QUEUED` `ResearchState` id, runs `run_research` in a FastAPI
    background task; `GET /api/v1/research/jobs/{id}` reads the snapshot (status + result) or 404s. A new
    in-memory `JobStore` service (`backend/app/services/jobs/`) owns the lifecycle and stores the canonical
    `ResearchState` (job id = `state.id`); held as a process-singleton on `app.state`. The sync `POST
    /research` endpoint is kept. **Single-process, non-durable by design** — durable/cross-worker store,
    streaming progress, and `CANCELLED` deferred. [ADR 0031](adrs/0031-async-job-store.md).
    - 🔨 **M13 (durable job store):** `SqliteJobStore` (`backend/app/services/jobs/sqlite_store.py`) — the
      durable backend ADR 0031 deferred. Same `enqueue`/`get`/`run` lifecycle, but persists the canonical
      `ResearchState` JSON (`model_dump_json`/`model_validate_json`) to a stdlib-`sqlite3` file keyed by job
      id, so jobs **survive process restarts**. A small `JobStoreBackend` protocol (`base.py`) is the
      injectable seam both backends satisfy; the in-memory `JobStore` stays the default and the API is
      unchanged. **Capability only — not yet wired** as the `app.state` default; still single-process (no
      cross-worker). [ADR 0040](adrs/0040-sqlite-job-store.md).
- 🔨 **M13 — API + job submission + frontend wiring.** Submit job, stream progress, render artifacts.
  - 🔨 **M13 (frontend):** Deep Research submission + results UI (`frontend/src/pages/ResearchPage.tsx`,
    `components/research/`, `types/research.ts`, `services/research.ts`). Typed `submitResearch` service
    (injectable transport, snake-case wire contract mirroring `ResearchState`), presentation decoupled
    from the API, findings rendered with honest `disputed`/`weakest_support` flags (§11). Ships a sample
    fixture so the surface renders before the submit route lands. Backend route + streaming deferred to
    the M13 (backend) PR.
    - 🔨 **M13 (frontend) — report + creator packet rendering.** Extends the results UI with the band-D
      publishing artifacts: `ReportView` (title/abstract/sections + a code-resolved References list from
      citations) with a **non-omittable** `CaveatsPanel` (always shown when caveats are present, §11),
      and `CreatorPacketView` (hooks/angles/narratives + code-derived key facts) with the
      unsafe-claim **warnings** flagged prominently and cross-linked to creative elements by shared
      `finding_ids`. Adds `Report`/`Citation`/`Caveat`/`CreatorPacket` (+ siblings) TS types and the
      `publishing` substate; fixture extended. `npm run build` not runnable in the offline sandbox.
    - 🔨 **M13 (frontend) — Studio view.** Operator control panel for producing + publishing videos
      (`frontend/src/pages/StudioPage.tsx`, `components/studio/`, `types/video.ts`, `services/video.ts`).
      Topic → kick off a video job → a `PipelineStages` strip (research → packet → script → render, each
      with its own `JobStatus`) → preview the report/packet (reusing the existing `ReportView`/
      `CreatorPacketView`) + a new `ScriptView` (narrative beats + `RenderedVideo` descriptor) → a
      `PublishPanel` and `SchedulePanel` calling typed, **mockable** service methods (`submitVideoJob`/
      `getVideoJob`/`publishVideo`/`scheduleVideo`, injectable transport, snake-case wire mirroring the
      planned `POST /api/v1/videos` + `MediaPlan`/`RenderedVideo`). The §11 publish gate is load-bearing:
      the packet's unsafe-claim warnings + the report's caveats render **inside** both the publish and
      schedule panels (a scheduled publish is a publish action), adjacent to the button, gating the
      action behind an explicit acknowledgment. Ships a `sampleVideo`
      fixture (reusing `sampleResearch`) so the surface renders offline; backend routes deferred to a
      sibling PR. `npm run build` passes (tsc + vite).

## Media Production Layer (CLAUDE.md §3.3 — second major component)
> Provider-neutral, deterministic **tools** (CLAUDE.md §4 — never agents). Introduced
> via [ADR 0019](adrs/0019-media-production-layer.md) per §3.4/§16.
- ✅ **Layer scaffold.** `backend/app/media/` seams mirroring the LLM/search fabric:
  `TTSProvider` protocol + hermetic `FakeTTSProvider` (text → `SynthesizedSpeech`);
  `CompositionService` protocol + hermetic `FakeCompositionService` wrapping the future
  FFmpeg step (assets → `RenderedVideo`, **no real ffmpeg**); and — asymmetric — a subtitle
  band shipping **real** code (sync `SubtitleService` protocol + `DeterministicSubtitleService`
  + pure stdlib `format_srt`/`format_vtt`). Typed `extra='forbid'` artifact DTOs (`aud_`/`sub_`/`vid_`)
  carry a required `produced_via` provenance string (symmetric with `discovered_via`/`extracted_via`).
  Layer imports nothing from the Deep Research schema (standalone). [ADR 0019](adrs/0019-media-production-layer.md).
- 🔨 **Concrete adapters.** Real `TTSProvider` (ElevenLabs/Azure), `CompositionService` (real ffmpeg),
  image/video generation-or-retrieval (Veo/stock) — behind the protocols, network/binary-gated.
- ✅ **Creator-packet → media handoff contract.** `MediaPipeline` (`backend/app/media/pipeline.py`) — a
  deterministic tool (no LLM) that maps a `CreatorPacket` to a `MediaPlan` (assembled-video descriptor):
  selects a `NarrativeOption` → synthesizes narration once (`TTSProvider`) → allocates caption timings by
  cumulative integer boundaries (invariant `cues[-1].end_ms == audio.duration_ms == video.duration_ms`) →
  builds the track (`DeterministicSubtitleService`) → composes (`CompositionService`). DI + skip/raise mirror
  `IngestionService`; the single, deliberate ADR 0019 §4 coupling exception (only this file imports the Deep
  Research schema). `visual_uris` pass through (sourcing still deferred). [ADR 0025](adrs/0025-media-pipeline.md).
  - ✅ **Composition (ffmpeg).** `FfmpegCompositionService` (`backend/app/media/composition/ffmpeg.py`) —
    first concrete `CompositionService`: assembles audio + `CaptionTrack` + visuals into a vertical MP4.
    Pure `build_ffmpeg_args` (argv construction, fully unit-testable with no binary) split from a single
    mockable `subprocess.run` execution seam; missing-binary/non-zero-exit → `CompositionError`
    (`shlex.join`'d command + stderr tail). Duration mirrors narration; captions burned in via
    `subtitles.format_srt`. Hermetic argv/error tests + `@pytest.mark.integration` real-render smoke
    (lavfi inputs, skips without ffmpeg). No new dependency. [ADR 0023](adrs/0023-ffmpeg-composition.md).
  - ⬜ **TTS / visuals.** Real `TTSProvider` (ElevenLabs/Azure) and image/video generation-or-retrieval
    (Veo/stock) — still network-gated behind their protocols.
  - ✅ **Visual / B-roll retrieval seam.** `backend/app/media/visuals/` — the retrieval half of the
    §3.3 "image/video retrieval" responsibility ADR 0019 deferred (the `visual_uris` producer for
    `CompositionService.render`). A `VisualProvider` protocol + a `VisualClip` DTO (`vis_`,
    `extra='forbid'`, required `produced_via`; `kind`/`width`/`height`/optional `duration_ms`/`attribution`)
    + a hermetic `FakeVisualProvider`, mirroring the search fabric (DTO beside its protocol in `base.py`).
    Real httpx `StockVisualProvider` over Pexels `GET /videos/search` (Brave-style hardening: key at
    construction, never leaked; injectable client; `per_page` clamp; `VisualError` only on bad shape).
    The tool, never the LLM, mints the asset `uri`. Offline `MockTransport` tests +
    `@pytest.mark.integration` live (`REEL_AUTOMATION_STOCK_API_KEY`). Adapter-only, no wiring/config
    change. [ADR 0024](adrs/0024-visual-retrieval.md).
- ⬜ **Concrete adapters.** Behind the ADR 0019 protocols, network/binary-gated.
  - ✅ **TTS:** `HttpTtsProvider` (httpx, generic REST `POST /synthesize` → raw audio bytes) — one
    adapter serves any compatible backend by config; bytes→`audio_uri` via an injected storage `sink`
    (descriptor-not-bytes invariant); `duration_ms` from an `X-Audio-Duration-Ms` header (fail-loud on
    absence); LLM-adapter hardening (`MockTransport`-tested, key-at-construction, integration smoke).
    [ADR 0022](adrs/0022-tts-adapter.md).
  - ⬜ **Composition** (real ffmpeg) and **image/video generation-or-retrieval** (Veo/stock).
- ⬜ **Creator-packet → media handoff contract.** Maps the Deep Research creator packet (M12) to media
  inputs; earns its own ADR once M12's packet shape is fixed.

## Publishing / Social-Ops Layer (CLAUDE.md §3.4 — fourth major component)
> Provider-neutral, deterministic **tools** (CLAUDE.md §4 — never agents). Uploads a finished
> `RenderedVideo` (Media layer artifact) to short-form platforms. Introduced via
> [ADR 0033](adrs/0033-publishing-layer.md) per §3.4.
- ✅ **Layer scaffold + first adapter.** `backend/app/publishing/` mirroring the search/visuals
  fabric: a `PublishingProvider` protocol + `PublishTarget`/`PublishResult` DTOs (strict, id-prefixed
  `pub_`, required `published_via` provenance; `privacy_status` defaults to `private`) + hermetic
  `FakePublishingProvider`, all in `base.py`. The provider — never an LLM — mints the platform post
  id/url (the §11 evidence boundary, publishing-side).
- ✅ **YouTube Shorts publisher.** Real httpx `YouTubeShortsPublisher` over the YouTube Data API v3
  **resumable upload** (initiate `POST …?uploadType=resumable&part=snippet,status` → read `Location`
  session header → `PUT` raw bytes → map `id` to a `watch?v=` url); `#Shorts` appended to the
  description. Storage-neutral via an injected `VideoSource` (read-side mirror of the TTS `AudioSink`).
  Hardened like Brave/TTS (ADR 0021/0022): OAuth token at construction (no `Settings`/`config.py`
  touch, no leak), bounded timeout, injectable client; operational failures propagate as `httpx`
  errors, only a malformed shape wraps in `PublishError`. `MockTransport`-tested offline; a
  side-effecting `@pytest.mark.integration` smoke test uploads a real `private` video, gated on
  `REEL_YOUTUBE_ACCESS_TOKEN` + `REEL_YOUTUBE_TEST_VIDEO`. [ADR 0033](adrs/0033-publishing-layer.md).
- ✅ **TikTok / Instagram Reels skeletons.** Protocol-conformant placeholders that raise
  `PublishError("adapter pending")` — document the intended §3.4 platform coverage; concrete
  create-container → publish adapters are drop-in follow-ups behind the protocol.
- ⬜ **OAuth token refresh, composition-root wiring (+ `Settings` keys), chunked upload, TikTok/IG
  concrete adapters.** Documented deferrals (ADR 0033) — added on demand, the wiring-free shape the
  search/visuals adapters shipped in.
## Automation / Orchestration fabric (CLAUDE.md §3.4 — future layer)
> Deterministic *tools* (CLAUDE.md §4 — scheduling/batch execution, never agents).
> Introduced via [ADR 0034](adrs/0034-scheduler.md) per §3.4/§16.
- ✅ **Scheduler primitives.** `backend/app/scheduler/` — the unattended N-videos/day loop's
  three composable, hermetic pieces: `Schedule` (**pure** `next_run_after(reference)`, no clock/sleep
  — `IntervalSchedule` anchored slots + `DailySchedule` UTC times-of-day; strictly-after semantics,
  naive-datetime rejection), `TopicQueue` (FIFO + priority backlog over `heapq` with a load-bearing
  `(priority, seq, topic)` key), and `BatchRunner` (runs an **injected** `Produce` coroutine across a
  batch under an `asyncio.Semaphore`, per-topic error isolation, submission-order `BatchResult`).
  Decoupled from the real `VideoPipeline` via the injected callable. Stdlib only (no apscheduler/celery).
  [ADR 0034](adrs/0034-scheduler.md).
- 🔨 **Driver loop + real wiring (deferred).** The long-lived `while: sleep until next_run; drain;
  run_batch` process (the only piece touching a real clock/sleep) and the binding of `Produce` to the
  real `VideoPipeline` + publishing step — the follow-up that makes the loop runnable end-to-end.

## Live providers (network-gated)
- 🔨 **M-LP — Concrete provider adapters.**
  - ✅ **M-LP.1 (LLM):** `OpenAICompatibleProvider` (httpx, OpenAI `/chat/completions`) —
    one adapter serves Groq/OpenRouter/Together/Cerebras/Ollama by config. `json_object` +
    schema-in-prompt + `model_validate_json` + error-fed retry. `build_router_from_settings`
    composition root + `python -m app.cli.plan` harness + `@pytest.mark.integration` live test.
    **The Planner is now runnable against a real free LLM.** [ADR 0007](adrs/0007-openai-compatible-llm-adapter.md).
  - ✅ **M-LP.2 (search):** `TavilySearchProvider` (httpx, Tavily `POST /search`) — first
    concrete `SearchProvider`, mirroring M-LP.1 (Bearer auth, MockTransport tests, integration
    smoke test). Maps hits → `SearchResult` (web-only); the tool, never the LLM, mints the
    `url`. Adds `Settings.search_api_key` (separate key) + `.env.example` block; adapter only,
    no wiring yet. **Source Discovery is now runnable end-to-end against a real backend.**
    [ADR 0013](adrs/0013-live-search-adapter.md).
  - ⬜ **M-LP.2 (search):** real `SearchProvider` adapter (unblocks Source Discovery end-to-end).
    - ✅ **Brave:** `BraveSearchProvider` (httpx, `GET /res/v1/web/search`, `X-Subscription-Token`) —
      a second concrete provider for failover/robustness, `web.results[]` → `SearchResult`, `count`
      clamped to 20, offline-tested via `MockTransport` + a `@pytest.mark.integration` smoke test.
      [ADR 0021](adrs/0021-brave-search-adapter.md).
  - ✅ **Provider registry:** `app/services/llm/providers.py` — a `name → ProviderPreset`
    registry (`groq`, `nvidia`, `huggingface`, local `ollama`) + `build_provider(name, settings)`.
    Operator selects a known backend by name (registry owns the `base_url`) and supplies only the
    key, so several providers' keys coexist in one `.env`. Builds the M-LP.1 adapter; additive
    alongside `factory.py`, no routing/wiring change. [ADR 0028](adrs/0028-provider-registry.md).
  - ⬜ **M-LP.3 (optional):** provider-SDK adapters (e.g. Gemini native `response_schema`) if
    free-model JSON reliability proves insufficient.
  - 🔨 **M-LP.4 (YouTube ingestion):** `TranscriptProvider` seam + `FakeTranscriptProvider`
    + real `YouTubeTranscriptProvider` (`youtube-transcript-api`, optional `[youtube]` extra,
    lazy-imported) wired into `IngestionService` for `SourceType.YOUTUBE`. Opens the YouTube
    path ADR 0008 deferred; pure `extract_video_id`/`normalize_transcript` helpers, timestamps
    discarded in v1. Hermetic + `@pytest.mark.integration` live. [ADR 0015](adrs/0015-youtube-ingestion.md).
  - ✅ **M-LP.4 (PDF ingestion):** second parser behind the ingestion seam — a `PdfParser`
    protocol + `pypdf`-backed `PypdfParser` (lazy import, offline-safe to construct) +
    `FakePdfParser`, routed for `SourceType.PDF` in `IngestionService`; content-type allowlist
    widened to `application/pdf`. Text-layer only; scanned/image-only PDFs (OCR) stay deferred.
    `Chunk.parsed_via` reconsidered and re-deferred to a dedicated schema PR (ADR 0008's gate).
    `pypdf` added to deps. [ADR 0014](adrs/0014-pdf-ingestion.md).
  - ✅ **M-LP.3 (LLM, Gemini-native):** `GeminiProvider` (httpx, `generateContent` REST) — the
    second concrete `ModelProvider`, whose value over M-LP.1 is **native structured output**:
    `responseSchema` + `responseMimeType: application/json` constrain decoding server-side
    (vs schema-in-prompt). A bounded `_to_gemini_schema` sanitizer inlines `$ref`/`$defs` and
    drops Gemini-rejected keys; `x-goog-api-key` header auth (no key in URL); one error-fed
    repair retry. Gemini-specific `Settings` (`gemini_api_key`/`gemini_base_url`/`gemini_model`);
    `httpx.MockTransport` unit tests (incl. nested-schema sanitization) + `@pytest.mark.integration`
    live smoke test. Router wiring is a trivial deferred follow-up. [ADR 0020](adrs/0020-gemini-native-adapter.md).
  - ✅ **M-LP (resilience):** LLM retry + policy-driven fallback (`services/llm/resilience.py`) — the
    deterministic *service* half of fault tolerance (CLAUDE.md §4), realizing the retries ADR 0005
    deferred and engaging ADR 0003's `FALLBACK` slot. A `ResilientModelProvider` decorator (bounded
    retry-with-backoff, `ModelProvider`-in/out drop-in) + a `complete_with_fallback` helper /
    `ResilientRouter` (one policy-driven `FALLBACK` hop on terminal primary failure — no retry-of-retry).
    Provider-neutral by injection (stdlib only; `retry_on` + async sleeper injected — the
    transient-vs-permanent narrowing is the wiring site's job); self-/no-fallback guards re-raise the
    primary error. Hermetic + deterministic (recording sleeper asserts the backoff schedule, raising
    fake asserts retry count + fallback). Capability only, no wiring. Reconciles with ADR 0005's
    node-level `RetryPolicy` (provider-level composes *under* the node); the "when to give up" judgment
    stays with the Orchestrator. [ADR 0027](adrs/0027-llm-resilience.md).

## Ops / Infrastructure
- ✅ **Containerization + deploy CI.** Multi-stage `backend/Dockerfile` (non-root, slim, uvicorn
  `app.main:app`, `/api/v1/health` HEALTHCHECK) + `frontend/Dockerfile` (node build → nginx
  static serve, SPA fallback) + root `docker-compose.yml` (backend + frontend, `depends_on`
  health, `env_file: backend/.env`) + per-context `.dockerignore` + a build-only
  `.github/workflows/docker-build.yml` (PR/push to `main`, no registry push). No app-code or
  `ci.yml` changes. **Run locally:** `cp backend/.env.example backend/.env && docker compose up
  --build` → backend `http://localhost:8000`, frontend `http://localhost:8080`. Images were
  authored offline (no Docker daemon in the sandbox); the first real `docker build` is deferred
  to a Docker-enabled run / the `docker-build.yml` CI job.
- ⬜ **Registry publish (deferred).** Push tagged images to GHCR on release once the deploy
  target is chosen; current workflow is build-only to keep zero auth/secret surface.
- ✅ **Structured logging + run-tracing scaffold.** Stdlib-only `JsonFormatter` (one JSON
  object per log line: `ts`/`level`/`logger`/`message`/`run_id`) + a `contextvars`-based
  `run_context(run_id)` so a Deep Research job's logs are correlatable across all nodes/agents
  without changing existing `getLogger(__name__)` call sites. `setup_logging(level, json=...)`
  configures the root logger idempotently; entrypoint wiring left as a one-line call.
  [ADR 0030](adrs/0030-structured-logging.md).

## Topic / Trend Sourcing (CLAUDE.md §3.4 — pipeline front door)
- ✅ **Topic / trend sourcing layer** (`backend/app/topics/`). The pipeline's front
  door: niche/seed → ranked candidate video topics (the backlog's trend-awareness ask).
  Both halves are *tools*, not agents (CLAUDE.md §4): a `TrendProvider` async protocol +
  `Source`-shaped `TopicIdea` DTO (auto-minted `topic_…` id + required `sourced_via` — the
  §11 anchor: tool-discovered, never LLM-invented), a hermetic `FakeTrendProvider`, an
  `httpx` `HttpTrendProvider` over a generic trends/keyword REST shape (mirrors the search
  adapters; MockTransport-hermetic + an integration smoke reading the key from the env, no
  `config.py` change), and a pure deterministic `select_topics` (rank by provider `signal`
  desc, explicit title/id tie-break, `None` lowest; de-dupe keeps the highest-signal idea
  wholesale). The green-light *judgment* is a future content-strategy agent (§5.6) that
  consumes this ordered output; the scheduler queue, a `Settings` field, and a provider
  router are deferred follow-ups. Local `_gen_id` copy (ADR 0019 precedent) keeps the layer
  standalone. [ADR 0037](adrs/0037-topic-trend-sourcing.md).

## Showcase
- 📄 **Deep Research engineering write-up** — `docs/showcase/deep-research-architecture.md`:
  the four bands, the full node pipeline, an accurate LangGraph Mermaid (revision cycle +
  failure sink), and the §11 evidence-vs-inference "made structural" pattern. Public-facing
  (CLAUDE.md §12). Tracks the engine through M10b.

## Analytics / feedback loop (CLAUDE.md §3.4 — fourth layer)
> Provider-neutral, deterministic **tools** (CLAUDE.md §4 — the *judgment* of which ranked
> topics to pursue stays with an upstream agent). Introduced via [ADR 0036](adrs/0036-analytics-feedback.md).
- ✅ **Stats seam + topic scorer.** `backend/app/analytics/` — pull published-video performance
  back in to steer what gets made next. (1) A seam mirroring the search fabric: an
  `AnalyticsProvider` protocol (`fetch_stats(*, post_id) -> VideoStats`) + a platform-pure
  `VideoStats` DTO (platform `post_id` as the natural key — no synthetic id; `fetched_via`/`fetched_at`
  provenance; watch-time absolute vs retention ratio kept distinct, both optional `| None`) + a
  hermetic `FakeAnalyticsProvider` + a real httpx `YouTubeAnalyticsProvider` (single verified
  `GET /v2/reports`, mapped by column name; Brave-style hardening — OAuth token at construction,
  never leaked, `AnalyticsError` only on bad shape/not-found). (2) `feedback.score_topics` — an
  explainable, **batch-independent** topic scorer (scale-free engagement/retention/views-saturation
  components blended with named weights; `TopicScore` carries the breakdown — §11; score-desc with a
  deterministic topic tie-break). Fully hermetic (`MockTransport` + pure scorer; live YouTube path is a
  `@pytest.mark.integration` smoke). Capability only — no wiring (no `config.py`/`api/` change); adoption
  is a later orchestrator/topic-queue change. [ADR 0036](adrs/0036-analytics-feedback.md).

---
*Updated 2026-06-05. Deep Research milestone: **M12** (Creator packet) next; M1–M11 built. Separately, the **Media Production Layer** (CLAUDE.md §3.3, second major component) is seam-scaffolded — see its section above and [ADR 0019](adrs/0019-media-production-layer.md).*
*Updated 2026-06-05. Reasoning band complete through **M10b** (bounded revision loop). M1–M10b
+ M-LP.1 (LLM adapter) implemented; the Planner runs live (Gemini/Groq), the pipeline
fetches+chunks real web sources, and synthesize→critique→(revise) runs end-to-end. Next:
M11 (report/export).*

> **Build-environment note:** the agent sandbox can reach **HTTP/API egress** (live LLM calls
> and web fetches work) but **not the pip/PyPI index** (no `pip install`). So milestones are
> verified hermetically (`Fake*` providers + `httpx.MockTransport`) plus `@pytest.mark.integration`
> live smoke tests; only deps requiring a fresh install (e.g. provider SDKs, OCR libs) defer to a
> network-enabled run. The provider adapters that *do* defer are collected under **M-LP**.
