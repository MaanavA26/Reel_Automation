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
  `Source.discovered_via`. Single-node acquire keeps the fan-out reducer deferred to M7.
  [ADR 0006](adrs/0006-source-discovery-and-search-fabric.md).
- ⬜ **M6 — Source Ingestion tool(s).** Deterministic fetch + parse (web/PDF/YouTube/repo) → `Chunk`s + normalization.
- ⬜ **M7 — Evidence Extraction agent.** chunks → `Evidence` with attached provenance + confidence.

## Knowledge Reasoning band
- ⬜ **M8 — Cross-Verification agent.** Corroborate claims across sources; contradiction/weak-support detection.
- ⬜ **M9 — Synthesis agent.** Evidence map → structured synthesis.
- ⬜ **M10 — Editorial Critic + revision loop.** Gap analysis, quality judgment, bounded revision cycles.

## Knowledge Publishing band
- ⬜ **M11 — Report + structured export generation.** Research report, evidence map, contradiction/caveat list.
- ⬜ **M12 — Creator packet + downstream handoff artifacts.** Hooks, angles, key facts, narrative options; unsafe-claim warnings.

## Surface
- ⬜ **M13 — API + job submission + frontend wiring.** Submit job, stream progress, render artifacts.

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

---
*Updated 2026-06-04. Current milestone: **M6** (Source Ingestion). M1–M5 merged to `main`; M-LP.1 (LLM adapter — Planner testable with a real key) in review.*

> **Build-environment note:** the agent sandbox has no outbound network, so milestones are
> built and verified offline against `FakeProvider` and constructed fixtures. The thin real
> provider adapters are collected into **M-LP** for a network-enabled run. Milestone branches
> are stacked PRs (each based on its predecessor) until merges drain the chain.
