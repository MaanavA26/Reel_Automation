# ADR 0020: Gemini-native LLM Provider Adapter (httpx, native structured output)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

M-LP.1 (ADR 0007) shipped the first concrete `ModelProvider`,
`OpenAICompatibleProvider`. It gets structured output by injecting the caller's
JSON Schema **into the prompt** and hoping the model returns conforming JSON,
backed by one error-fed repair retry — deliberately portable across free
OpenAI-compatible backends, but not server-enforced. ADR 0007 and ADR 0003 both
named a provider-SDK adapter with **native** structured output (Gemini's
`responseSchema`) as a fast-follow (ROADMAP M-LP.3) for when schema fidelity
matters. Repo memory records Gemini as a preferred, working provider.

The constraint that shaped ADR 0007 still holds: the build sandbox has API/HTTP
egress but **no pip/PyPI index**, so the official `google-genai` SDK cannot be
installed, imported, or type-checked here. `httpx` is already a runtime
dependency. A new adapter must therefore be fully offline-verifiable without a
new dependency.

## Decision

**Ship a second concrete `ModelProvider`, `GeminiProvider`
(`services/llm/gemini.py`, `name = "gemini"`), built on `httpx` over the Gemini
`generateContent` REST API.** Its value over M-LP.1 is **native structured
output**: the request sets `generationConfig.responseMimeType = "application/json"`
and `generationConfig.responseSchema`, so Gemini constrains decoding to the
schema server-side rather than relying on prompt instructions. The response is
still `model_validate_json`'d (the wire schema is an OpenAPI subset, not our full
Pydantic contract), and **one error-fed repair retry** is kept to mirror ADR
0007's reliability boundary exactly.

- **Schema sanitization (`_to_gemini_schema`).** Gemini's `responseSchema` is an
  OpenAPI 3.0 subset, not JSON Schema. A raw `model_json_schema()` carries
  `$defs`/`$ref` (for nested models) and keys (`title`, `additionalProperties`,
  `default`, `$schema`) that Gemini **400s** on. A bounded, private helper
  recursively **inlines** `$ref` targets from `$defs`, drops the unsupported
  keys, and collapses `Optional[X]`'s `anyOf:[X, null]` to a single `nullable`
  branch. This is intentionally not a general JSON-Schema compiler.
- **Auth.** The API key is sent as the `x-goog-api-key` **header**, never the
  `?key=` query parameter, so it cannot leak into request-URL logs. The key is
  read from `Settings` (a `SecretStr`) by the caller.
- **Config (additive).** `Settings` gains `gemini_api_key: SecretStr`,
  `gemini_base_url` (default `https://generativelanguage.googleapis.com`), and
  `gemini_model` (default `gemini-2.5-flash`). These are **separate** from the
  shared `base_url`/`api_key` (documented as the OpenAI-compatible adapter's), so
  both providers can coexist in one `.env`.

### Scope boundary — adapter only; router wiring deferred

This change is the **adapter file + additive settings only**. The composition
root `factory._build_provider` is **deliberately not touched**: `GeminiProvider`
is not yet reachable via `default_provider`. Wiring is a trivial one-branch
follow-up (`if name == GeminiProvider.name: return GeminiProvider(...)`),
deferred to keep this diff scoped and reviewable. Until then the adapter is
constructed directly (as the integration test does).

### Retry boundary (unchanged from ADR 0007)

Parse-repair retry (non-conforming JSON) is the adapter's responsibility.
*Operational* retry (429, timeout, budget, failover) is the Orchestrator's: the
adapter surfaces those as raised `httpx`/`GeminiError` and does not swallow them.
Notably a malformed-schema **400 propagates immediately** via `raise_for_status`
— it is not catchable by the validation repair loop, which is why the sanitizer
being correct is load-bearing.

## Consequences

### Positive

- **Server-enforced schema fidelity** for the structured-output contract, a
  meaningful robustness gain over schema-in-prompt — especially for nested
  schemas and smaller/faster flash models.
- **Fully offline-verifiable** via `httpx.MockTransport`, no new dependency:
  request building, header auth, the response→schema mapping, the safety-blocked
  (no-candidates) path, the error-fed retry, and — critically — the schema
  sanitizer **against a nested model** are all unit-tested. The live call is a
  `@pytest.mark.integration` smoke test, skipped without a key.
- Provider-neutral fabric vindicated: a second provider lands behind the same
  `ModelProvider` protocol with **zero agent change**.

### Negative

- **Live behavior is unverifiable offline.** Whether the sanitized schema is
  accepted for every real Pydantic model can only be confirmed live; the nested
  offline test proves the *shape* (`$ref`/`$defs`/`title` gone), not acceptance.
- The sanitizer is **bounded**: exotic JSON-Schema constructs (e.g.
  `patternProperties`, `allOf`, deeply recursive `$ref` cycles) are not handled.
  Our current schemas don't use them; revisit if one does.
- The default model id `gemini-2.5-flash` **cannot be verified current offline**
  (mirrors ADR 0007's Groq-id caveat); it is config, so correcting it is a
  one-line `.env` change.

### Neutral

- `default_provider` is unchanged; Gemini is opt-in by direct construction until
  the deferred factory branch lands.

## Alternatives considered

### Option A — Use the official `google-genai` SDK

**Pros:** native types, less request-plumbing, SDK owns the schema translation.
**Cons:** cannot be installed / imported / type-checked in the sandbox (the exact
deadlock ADR 0003/0007 avoided); heavyweight for one POST. **Why rejected:**
offline verifiability is decisive and `httpx` makes one POST trivial.

### Option B — Reuse the OpenAI-compatible adapter against Gemini's OpenAI-compat endpoint

**Pros:** zero new code. **Cons:** forfeits the **entire point** — native
`responseSchema` — falling back to schema-in-prompt, which M-LP.1 already covers.
**Why rejected:** delivers no value over the existing adapter.

### Option C — Send the raw `model_json_schema()` as `responseSchema`

**Pros:** no sanitizer. **Cons:** Gemini 400s on `$ref`/`$defs`/`title`/etc., and
that 400 is a hard failure the repair loop never sees. **Why rejected:** the
adapter would not work for any nested schema; the sanitizer is mandatory.

## References

- Related: [ADR 0007](0007-openai-compatible-llm-adapter.md) (the first adapter
  and the structured-output/retry pattern this mirrors), [ADR 0003](0003-model-router-llm-fabric.md)
  (the provider-neutral fabric this fills a second slot in), [ADR 0002](0002-langgraph-workflow-integration.md)
  (async contract), [ADR 0005](0005-workflow-error-handling.md) (operational-retry
  ownership boundary).
- [CLAUDE.md](../../CLAUDE.md) §6 (model routing), §4 (agent-vs-tool), §5.7
  (provider abstraction), §9/§11.
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP.3 (this).
