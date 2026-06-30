# ADR 0056: Multi-provider, capability-tiered model fabric

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Tech Lead
- **Supersedes:** none (extends ADR 0003 router, ADR 0028 registry, ADR 0055 retry)
- **Superseded by:** none

## Context

The composition root wired **one** LLM provider for **all** roles
(`default_provider`). Two limits followed (issue #113):

1. **No capacity pooling.** Free-tier single-provider runs throttle (Groq stalled
   under the extraction burst). The same capable model is free on *multiple*
   providers (verified 2026-06-19: NVIDIA `meta/llama-3.3-70b-instruct`,
   Groq 70b, HF router — NVIDIA is independent infra; HF may proxy to Groq), so
   different providers are often **independent rate-limit buckets**.
2. **No capability tiering.** A single model must serve both bulk, mechanical
   work (per-chunk extraction — cheap, high volume) and judgment-heavy work
   (synthesis/report/**creator packet / script**). A small local model fails the
   judgment work (it mis-grounded the creator-packet narrative finding-references
   → empty narratives → no video); a big model is wasteful on the bulk work.

`ModelRouter` (ADR 0003) *already* accepts `providers: dict[name -> provider]` +
a per-role `RolePolicy`. The gap was purely the composition root.

## Decision

**Per-role provider selection, wired end to end.**

- **Config.** Add `planning_provider` / `extraction_provider` /
  `long_context_provider` / `fallback_provider` (empty ⇒ `default_provider`).
  Per-role *models* already existed. A role is now `(provider, model)`.
- **Policy.** `default_policy` maps each role to `ModelChoice(role_provider or
  default_provider, role_model)`.
- **Composition.** `_build_router` builds **every distinct provider** the policy
  references (deduped — three roles sharing `nvidia` build it once), registers
  each under its **config name** (the name the policy keys by; a preset adapter's
  own `.name` is always `"openai-compatible"`), wraps each per the ADR 0055 retry
  gate, and returns the tuple of *inner* providers for lifecycle close (ADR 0044).
- **Schema-constrained decoding.** The OpenAI-compatible adapter gains an opt-in
  `use_schema_format` that sends the caller's JSON Schema as a `json_schema`
  `response_format`, so the backend *constrains* output to valid matching JSON.
  Enabled per provider via `llm_schema_format_providers` (default `ollama`): small
  local models need it to satisfy strict Pydantic schemas; capable cloud models
  ground fine without it (and may not support it).

**MVP scope:** static per-role assignment. Capacity-aware / health-aware
round-robin across providers *for one role* is a deferred follow-up; the
eval harness (ADR 0029) is the intended driver for which model wins each role.

## Consequences

- A run can now route bulk extraction to a local 3B (Ollama, schema-constrained)
  and the judgment stages to a free cloud 70B (NVIDIA) — fixing the narrative
  grounding failure **without a local 7B** (gentler on small machines: cloud =
  network only). Pooling distinct providers multiplies effective free throughput.
- Backwards-compatible: empty per-role providers reproduce the prior
  single-provider behavior; the whole hermetic suite is unchanged (998 pass).
- `_build_router` now returns `(router, tuple[providers])`; both call sites
  (research + media deps) close every inner provider.

## Alternatives considered

- **Local 7B for the judgment stages.** Better grounding, but ~4.7GB on an 8GB
  machine = swap/heat; cloud 70B is more capable *and* gentler. Rejected as the
  default; still selectable via config.
- **One stronger model for everything (cloud).** Simpler, but wastes a 70B on
  bulk per-chunk extraction and concentrates load on one rate bucket. Tiering is
  cheaper and spreads load.
- **LLM "supervisor" to pick providers at runtime.** Rejected (CLAUDE.md §4):
  routing is procedural; an LLM in the hot path adds latency, a rate-limited
  dependency, and burns the quota we're conserving. A deterministic policy +
  (later) health-aware round-robin is the right tool.
- **Always-on schema-constrained decoding.** Rejected: not portable across all
  OpenAI-compatible backends; opt-in per provider instead.

## Honest caveats

Free tiers aren't infinite (Gemini already 429s); provider independence varies
(HF↔Groq); sustained free-tier aggregation is a bootstrap accelerator, not a
production foundation (ToS, changing limits) — keep a paid/local floor for scale.
