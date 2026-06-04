# Changelog

All notable changes to Reel Automation are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries under `[Unreleased]` describe changes that have landed on `main` but
are not yet part of a tagged release. When cutting a release, move the
`[Unreleased]` section under a new version heading with the release date.

## [Unreleased]

### Added

- Apache License 2.0. `LICENSE` file at repo root; SPDX metadata in `backend/pyproject.toml` and `frontend/package.json`; reference in README.
- Deep Research state and provenance schema (`backend/app/schemas/research_state.py`) — `ResearchState`, `KnowledgeAcquisitionState`, `Source`, `Chunk`, `Evidence` with attached (inline) provenance, type-prefixed opaque IDs, and timezone-aware UTC timestamps. Decisions documented in [ADR 0001](docs/adrs/0001-research-state-and-provenance.md). First Phase 0 feature.
- Research Control band schema (`ResearchPlan`, `SubQuestion`) added to `backend/app/schemas/research_state.py`; `ResearchState` now includes a `plan` substate alongside `acquisition`. Second Phase 0 feature.
- Deep Research workflow skeleton (`backend/app/workflows/deep_research.py`) — a compiled LangGraph graph wiring `ResearchState` through async lifecycle-stub nodes (plan → acquire → reason → publish) that runs end-to-end (`queued → completed`). Establishes the node I/O contract (partial-state-update returns) every later agent/tool node plugs into. Decisions and the empirically-grounded fan-out deferral documented in [ADR 0002](docs/adrs/0002-langgraph-workflow-integration.md). Adds the `langgraph` dependency. Roadmap milestone M1; see [docs/ROADMAP.md](docs/ROADMAP.md).
- Model router / LLM fabric (`backend/app/services/llm/`) — provider-neutral, policy-driven role-based model selection: a `ModelProvider` protocol returning schema-validated structured output, a `ModelRouter` resolving `ModelRole` → `BoundModel` via a config-sourced `default_policy`, and a hermetic `FakeProvider` for tests. Per-role model ids added to `Settings`. Concrete provider adapters are batched into the network-gated M-LP milestone. Decisions in [ADR 0003](docs/adrs/0003-model-router-llm-fabric.md). Roadmap milestone M2.
- Research Planner agent (`backend/app/agents/research_planner.py`) — the first real reasoning node: `ResearchPlannerAgent` calls the model fabric (`PLANNING` role), maps a model-output DTO into the canonical `ResearchPlan`/`SubQuestion` schema (ids/timestamps minted by the schema, never the model), and raises `PlannerError` on an empty plan. Wired into the workflow `plan` node via factory-closure dependency injection ([ADR 0004](docs/adrs/0004-node-dependency-injection.md)); `run_research(state, *, planner)` now injects dependencies. Fully verified offline via `FakeProvider`. Roadmap milestone M3.
- Workflow error handling + conditional routing (`backend/app/workflows/deep_research.py`) — a deterministic failure path: a uniform `_with_failure_handling` wrapper converts any node exception (e.g. `PlannerError`) into a `FAILED` state update, and the graph's first **conditional edges** route off the typed `status` channel to a terminal `failed` sink. Adds a nullable `error: str | None` field to `ResearchState`. Resolves the agent-vs-deterministic orchestrator question (M4 is deterministic; judgment/quality-gates are deferred to M10). Decisions and deferrals in [ADR 0005](docs/adrs/0005-workflow-error-handling.md). Roadmap milestone M4 (failure path).

## [0.1.0] - 2026-03-29

### Added

- Initial Reel Automation scaffold.
- FastAPI backend application bootstrap with a versioned health endpoint at `/api/v1/health`.
- Typed configuration layer via `pydantic-settings` under `backend/app/core/`.
- Module boundaries for the future Deep Research layer: `backend/app/agents/`, `backend/app/services/`, `backend/app/tools/`, `backend/app/workflows/` (placeholders).
- React + Vite + TypeScript frontend shell with API service abstraction and a home page.
- Operating contracts: `CLAUDE.md`, `ARCHITECTURE.md`, `AGENTS.md`, `README.md`.
- Empty placeholders for `docs/adrs/` and `docs/standards/`.
- Backend test scaffold with a passing health-endpoint test.
