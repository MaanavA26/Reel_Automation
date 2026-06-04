# ADR 0007: First LLM Provider Adapter (OpenAI-compatible, httpx)

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Tech Lead, Council (httpx-OpenAI-compat / provider-SDK / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

M2 (ADR 0003) shipped the provider-neutral model fabric — the `ModelProvider`
protocol, `ModelRouter`, policy, and a `FakeProvider` — and **deferred the
concrete adapter** because the build sandbox has no network. Every agent
(Planner M3, Source Discovery M5) is built against that fabric but has never
made a real model call. To let the project actually run — and for the user to
test with a free API key — one concrete `ModelProvider` must land.

Constraints: the sandbox has no network and cannot install provider SDKs
(`anthropic`/`openai`/`google-genai` all `ImportError`), but **`httpx` is
installed** (now a runtime dependency). The user's runtime has network + a key.

## Decision

**Ship one OpenAI-compatible adapter built on `httpx`:
`OpenAICompatibleProvider` (`services/llm/openai_compatible.py`,
`name = "openai-compatible"`).**

- It speaks the OpenAI `/chat/completions` API, so a *single* adapter serves
  **Groq, OpenRouter, Together, Cerebras, and local Ollama** — selected by
  `base_url` + `api_key` + `model` (configuration only). This *is* CLAUDE.md §6
  policy-driven routing: switching provider is a config change, not new code.
- **Structured output:** JSON-object mode (`response_format={"type":"json_object"}`)
  + the caller's JSON Schema injected into the system prompt +
  `schema.model_validate_json`, with **one error-fed repair retry** (on failure,
  the model is shown its bad output and the `ValidationError` and asked to fix).
  Strict `json_schema` mode is **not** relied upon — it is not portable across
  the free backends targeted.
- **Wiring:** a composition root `build_router_from_settings(settings)`
  (`services/llm/factory.py`) maps `default_provider` → adapter and registers it
  under its `name`. A CLI (`python -m app.cli.plan "<topic>"`) runs the Planner
  end-to-end. `Settings` gains `base_url: str` and `api_key: SecretStr` and loads
  a local `.env` (see `.env.example`); `httpx` and `python-dotenv` become runtime
  dependencies.

### Retry boundary

Parse-repair retry (malformed/non-conforming JSON) is the **adapter's**
responsibility — intrinsic to honoring the `-> StructuredT` contract.
*Operational* retry (429 rate-limit, timeout, budget, provider failover) is the
**Orchestrator's** (M4, deferred per ADR 0003/0005): the adapter surfaces those
as raised `httpx`/`OpenAICompatError` and does not swallow them.

## Consequences

### Positive

- **The Planner is testable with a real LLM now**, using just a free key. The
  full path (config → router → adapter → live model → schema validation →
  `ResearchPlan`) runs end-to-end.
- Provider choice is a config swap (`base_url`/`key`/`model`) — no lock-in; the
  §6 anti-"chaotic multi-model" stance holds because the policy is the single
  control point.
- The adapter is **fully offline-verifiable**: request building, response→schema
  mapping, the fence/prose-stripping, and the error-fed retry are unit-tested via
  `httpx.MockTransport` (no network, no new dependency beyond httpx). The live
  call is a `@pytest.mark.integration` smoke test, skipped without a key.

### Negative

- **Live model behavior is unverifiable offline.** Whether a given free model
  returns schema-valid JSON for our prompts can only be confirmed by the user's
  live run. The bounded error-fed retry mitigates small-model JSON flakiness but
  does not eliminate it; very small models (e.g. 8B) will still fail more often —
  prefer a 70B-class model for the Planner.
- The recommended Groq model id (`llama-3.3-70b-versatile`) **cannot be verified
  current offline**; treat it as a starting default to confirm against the
  provider's live model list. It is config, so correcting it is a one-line `.env`
  change.

### Neutral

- `default_provider` in code stays `"anthropic"` (the intended production
  primary); `.env.example` sets `openai-compatible` for free local testing. The
  two configurations are cleanly separated.

## Honest test surface (what a key unlocks)

- **Research Planner — testable now** with only an LLM key (`python -m app.cli.plan`).
- **Source Discovery — NOT fully testable yet.** It plans queries via the LLM
  (testable) but retrieves via a `SearchProvider`, for which only
  `FakeSearchProvider` exists — a real search adapter is a separate
  network-gated milestone. With a key you exercise query-planning; the URLs
  remain faked.

## Alternatives considered

### Option B — Provider-specific SDK adapters (Gemini/Anthropic), native structured output

**Pros:** native `response_schema` (Gemini) / tool-use (Anthropic) enforce the
structured-output contract server-side, reducing parse failures. **Cons:** the
SDKs cannot be installed, imported, or mypy-checked in the sandbox — the adapter
would ship entirely unverifiable here (the exact deadlock ADR 0003 deferred to
avoid); one module + optional extra + mypy override per provider (sprawl).
**Why not first:** offline verifiability is decisive. The fabric is
provider-neutral, so an SDK adapter (e.g. Gemini for native schema fidelity) can
be added later behind the same protocol with no agent change — a fast follow,
not a reversal.

### Option C — Use the `openai` SDK instead of raw httpx

**Pros:** less request-plumbing. **Cons:** can't install/type-check offline;
heavyweight for one POST; same verifiability problem as B. **Why rejected:**
httpx (installed) gives offline testability and one POST is trivial.

## Deferred

- SDK adapters (Gemini native `response_schema`, Anthropic) — fast follow if
  JSON reliability on free models proves insufficient.
- Real `SearchProvider` adapter (unblocks Source Discovery end-to-end).
- Operational retries / budgets / failover → Orchestrator (M4).
- Streaming, tool-use, embeddings, token/cost accounting — added with consumers.

## References

- Related: [ADR 0003](0003-model-router-llm-fabric.md) (the fabric this adapter
  fills; "infra not a live slice" → now a live slice), [ADR 0002](0002-langgraph-workflow-integration.md)
  (async contract), [ADR 0005](0005-workflow-error-handling.md) (operational-retry
  ownership boundary).
- [CLAUDE.md](../../CLAUDE.md) §6 (model routing), §4 (agent-vs-tool), §7/§13.
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP (this is its LLM half; search adapter
  pending), M3 (the Planner this lets you test).
- [`backend/.env.example`](../../backend/.env.example) — run recipe.
