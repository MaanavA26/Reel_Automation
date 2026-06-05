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
  (`…→critique→report→publish`); `publish` is now the lifecycle terminal. Markdown rendering +
  creator-packet fields deferred to M12. [ADR 0017](adrs/0017-report-generation.md).
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
- 🔨 **M13 — API + job submission + frontend wiring.** Submit job, stream progress, render artifacts.
  - 🔨 **M13 (frontend):** Deep Research submission + results UI (`frontend/src/pages/ResearchPage.tsx`,
    `components/research/`, `types/research.ts`, `services/research.ts`). Typed `submitResearch` service
    (injectable transport, snake-case wire contract mirroring `ResearchState`), presentation decoupled
    from the API, findings rendered with honest `disputed`/`weakest_support` flags (§11). Ships a sample
    fixture so the surface renders before the submit route lands. Backend route + streaming deferred to
    the M13 (backend) PR.

## Live providers (network-gated)
- 🔨 **M-LP — Concrete provider adapters.**
  - ✅ **M-LP.1 (LLM):** `OpenAICompatibleProvider` (httpx, OpenAI `/chat/completions`) —
    one adapter serves Groq/OpenRouter/Together/Cerebras/Ollama by config. `json_object` +
    schema-in-prompt + `model_validate_json` + error-fed retry. `build_router_from_settings`
    composition root + `python -m app.cli.plan` harness + `@pytest.mark.integration` live test.
    **The Planner is now runnable against a real free LLM.** [ADR 0007](adrs/0007-openai-compatible-llm-adapter.md).
  - ⬜ **M-LP.2 (search):** real `SearchProvider` adapter (unblocks Source Discovery end-to-end).
  - ⬜ **M-LP.3 (optional):** provider-SDK adapters (e.g. Gemini native `response_schema`) if
    free-model JSON reliability proves insufficient.
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
## Showcase
- 📄 **Deep Research engineering write-up** — `docs/showcase/deep-research-architecture.md`:
  the four bands, the full node pipeline, an accurate LangGraph Mermaid (revision cycle +
  failure sink), and the §11 evidence-vs-inference "made structural" pattern. Public-facing
  (CLAUDE.md §12). Tracks the engine through M10b.

---
*Updated 2026-06-05. Reasoning band complete through **M10b** (bounded revision loop). M1–M10b
+ M-LP.1 (LLM adapter) implemented; the Planner runs live (Gemini/Groq), the pipeline
fetches+chunks real web sources, and synthesize→critique→(revise) runs end-to-end. Next:
M11 (report/export).*

> **Build-environment note:** the agent sandbox can reach **HTTP/API egress** (live LLM calls
> and web fetches work) but **not the pip/PyPI index** (no `pip install`). So milestones are
> verified hermetically (`Fake*` providers + `httpx.MockTransport`) plus `@pytest.mark.integration`
> live smoke tests; only deps requiring a fresh install (e.g. provider SDKs, OCR libs) defer to a
> network-enabled run. The provider adapters that *do* defer are collected under **M-LP**.
