# ADR 0003: Model Router and LLM Fabric

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** Tech Lead, Council (advisor + design sub-agents)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M2 (per [`docs/ROADMAP.md`](../ROADMAP.md)) introduces the layer every
Deep Research agent depends on: a way to call an LLM. CLAUDE.md §6 is explicit
that this must be a **policy-driven model router**, not "uncontrolled
multi-model chatter" — different models are selected by *role*, behind a
provider interface, so the system can mix providers and tiers deliberately.

Two questions must be settled before the first agent (the Research Planner, M3)
is written:

1. **Where does model selection live** — is it agent logic or service logic?
2. **What is the call contract** an agent uses, and how is it testable without
   network access or a live provider?

CLAUDE.md §4 draws a hard line: API wrapping and deterministic policy selection
are *tool/service* work; reasoning is *agent* work. The router sits squarely on
the service side.

## Decision

**We add a provider-neutral, policy-driven model router as a service under
`app/services/llm/`.** It has four parts:

1. **`ModelRole`** (`base.py`) — a `StrEnum` of logical roles
   (`PLANNING`, `EXTRACTION`, `LONG_CONTEXT`, `FALLBACK`). Agents request a role,
   never a concrete model.
2. **`ModelProvider`** (`base.py`) — a `Protocol` with a single async method
   `complete_structured(*, model, system, prompt, schema) -> schema-instance`.
   The provider coerces the model response into the caller's Pydantic schema
   (JSON mode / tool-calling is the adapter's concern); agents never parse raw
   text. Async to match the workflow node contract (ADR 0002).
3. **`ModelRouter` + `RolePolicy` + `BoundModel`** (`router.py`) — the router
   holds a registry of named providers and a `role -> ModelChoice(provider,
   model)` policy. `for_role(role)` resolves to a `BoundModel` (provider + model
   id) ready to call, or raises a typed `UnknownRoleError` /
   `UnknownProviderError`. Selection is pure dictionary lookup.
4. **`default_policy(settings)`** (`policy.py`) — builds the role->model map from
   `Settings` (per-role model ids + a default provider name), keeping policy as
   *configuration data*, overridable via `REEL_AUTOMATION_*` env vars.

A **`FakeProvider`** (`fakes.py`) implements the protocol with scripted,
schema-validated responses and call recording, so the entire fabric is testable
hermetically (no network, no API key).

### Scope boundary — the concrete adapter lands with its first consumer (M3)

M2 ships the **fabric and the contract**, not a live provider adapter. The first
concrete adapter (Anthropic) lands in M3, where the Research Planner agent is its
first real consumer. Rationale:

- **No consumer yet.** Building and wiring a provider adapter before any agent
  calls it is speculative (CLAUDE.md §7/§13). An adapter has no meaningful
  registration until a node uses it.
- **Verifiability and CI hygiene.** A real adapter requires its SDK present
  wherever tests and `mypy` run. Adding an unused provider SDK to the install /
  type-check path now would force `ignore_missing_imports` overrides or optional-
  extra import guards for zero current benefit. (The current build environment
  also cannot install the SDK, which corroborates the call but is not its basis.)
- **Architecture principle.** CLAUDE.md §5.7 says capabilities should be
  abstracted behind provider interfaces, not hard-coded. Shipping the interface
  first, the adapter with its consumer, is that principle applied.

`FALLBACK` is defined as a policy slot in M2; the *trigger* logic (fall back on
failure / budget exhaustion) is owned by the Orchestrator (M4), not the router.

## Consequences

### Positive

- Agents (M3+) depend only on `ModelRole` + `BoundModel.complete_structured`;
  swapping providers or tiers is a policy/config change, not an agent change —
  the controlled routing CLAUDE.md §6 requires.
- The fabric is fully runnable and type-checkable with no provider SDK and no
  network, in both the dev environment and CI; tests are hermetic via
  `FakeProvider`.
- Structured-output-at-the-boundary keeps agents free of parsing logic and
  preserves the typed contracts the project prizes.

### Negative

- **M2 as shipped is infrastructure, not a live slice.** `default_policy` maps
  roles to the `anthropic` provider, which is not registered until M3, so
  `for_role` raises `UnknownProviderError` in any non-test use. This is the
  intended, *tested* contract (fail loud, not mis-route) — but a reader should
  not mistake M2 for an end-to-end working LLM call. The first live call arrives
  with M3.
- The `ModelProvider` protocol is deliberately minimal (one method). Streaming,
  tool-use, embeddings, and token/cost accounting are not modelled yet; they are
  added when a consumer needs them (avoids speculative surface).

### Neutral

- Role taxonomy (4 roles) and the default model ids are a starting policy; both
  are config and expected to evolve as real workloads reveal the right tiers.

## Alternatives considered

### Option A — Ship a real Anthropic adapter in M2

Include `anthropic.py` with a faked-SDK unit test and a live
`@pytest.mark.integration` test. **Pros:** M2 is a complete, demoable slice
(the council's stated intent). **Cons:** requires the SDK on the test/type-check
path for a module with no consumer yet; not installable in the current build
environment; forces CI/mypy contortions or optional-extra guards prematurely.
**Why deferred (not rejected):** the adapter is still built — just in M3 with its
first consumer, where it can be wired, registered, and exercised end-to-end.

### Option B — Let agents call provider SDKs directly (no router)

**Pros:** less indirection now. **Cons:** hard-codes provider choice into agent
reasoning code, violating CLAUDE.md §4 (agent/tool separation) and §6 (policy-
driven routing); makes provider/tier changes a cross-cutting edit. **Why
rejected:** the router is the explicit architectural requirement.

### Option C — Free-text completion contract (parse in the agent)

A `complete(prompt) -> str` provider, with each agent parsing output. **Pros:**
simplest provider interface. **Cons:** pushes fragile parsing into every agent,
loses the typed-contract guarantee, and duplicates JSON-coercion logic. **Why
rejected:** structured-output-at-the-boundary is cleaner and keeps agents
reasoning-only.

## References

- Related: [ADR 0002 — LangGraph Workflow Integration](0002-langgraph-workflow-integration.md)
  (async node contract that the async provider method mirrors).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M2 (this), M3 (Planner + first adapter),
  M4 (Orchestrator owns fallback/retry/budget triggers).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (model routing), §5.7
  (provider abstraction), §7/§13 (no speculative overbuild).
