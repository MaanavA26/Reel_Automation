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
- ⬜ **M4 — Research Orchestrator.** Job lifecycle, status transitions, budgets, retries, progress, quality gates.

## Knowledge Acquisition band
- ⬜ **M5 — Source Discovery agent.** sub-questions → candidate `Source`s (search/query planning).
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
- ⬜ **M-LP — Concrete LLM provider adapters.** Implement the real `ModelProvider` adapters
  (Anthropic first) behind the M2 fabric + register them in the default policy, with
  `@pytest.mark.integration` smoke tests. Batched here because the build sandbox has no
  network to install/run provider SDKs; verified in a network-enabled session or in CI.
  Everything upstream is built against the provider abstraction + `FakeProvider`, so this
  milestone is thin and isolated.

---
*Updated 2026-06-01. Current milestone: **M4** (Research Orchestrator — job lifecycle, error/retry).*

> **Build-environment note:** the agent sandbox has no outbound network, so milestones are
> built and verified offline against `FakeProvider` and constructed fixtures. The thin real
> provider adapters are collected into **M-LP** for a network-enabled run. Milestone branches
> are stacked PRs (each based on its predecessor) until merges drain the chain.
