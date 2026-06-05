# ADR 0028: Named Provider Registry for OpenAI-compatible Backends

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0007 shipped one `OpenAICompatibleProvider` (`httpx`, OpenAI
`/chat/completions`) that serves *any* compatible backend — Groq, NVIDIA build,
HuggingFace router, local Ollama — selected entirely by `base_url` + `api_key` +
`model`. The composition root `build_router_from_settings` (`factory.py`) wires
exactly one such backend: the generic slot `Settings.base_url` + `Settings.api_key`,
which the operator fills with a hand-typed URL.

That generic slot has two ergonomic gaps for an operator who wants to use one of
the *known* free backends:

1. They must look up and paste the correct `base_url` (e.g. is it
   `https://router.huggingface.co/v1` or `/api/v1`?).
2. There is one `api_key` slot, so two backends' keys cannot coexist in one
   `.env` — switching backend is a URL **and** key edit, not a name change.

CLAUDE.md §6 wants provider choice to be controlled, policy-driven configuration,
not chaos — but it should also be *easy to select a known provider by name*.

## Decision

**Add a small name→preset registry, `app/services/llm/providers.py`, mapping each
known OpenAI-compatible backend to its preset `base_url`, plus a
`build_provider(name, settings) -> OpenAICompatibleProvider` factory.**

- `PROVIDER_REGISTRY: dict[str, ProviderPreset]` holds the four presets:
  `groq` (`https://api.groq.com/openai/v1`),
  `nvidia` (`https://integrate.api.nvidia.com/v1`),
  `huggingface` (`https://router.huggingface.co/v1`),
  `ollama` (`http://localhost:11434/v1`).
- `ProviderPreset` is a frozen dataclass: `base_url` + `api_key:
  Callable[[Settings], SecretStr]` (a callable, not a stringly-typed attr name,
  so the wiring is type-checked) + a `requires_key` flag.
- **The registry owns the URL; config owns only the key.** `Settings` gains three
  per-provider SecretStr keys — `groq_api_key`, `nvidia_api_key`,
  `huggingface_api_key` (empty by default). Ollama is local and keyless (the API
  accepts any bearer token), so it carries a non-empty placeholder and adds *no*
  config field. The per-role **model** ids stay where they are (policy-routed);
  `build_provider` takes no model arg — `complete_structured(model=...)` is
  per-call.
- `build_provider` looks the preset up, reads its key from `Settings`, and returns
  a configured `OpenAICompatibleProvider`. It **fails loud at build time**
  (mirroring `factory._build_provider` and the adapter's empty-`base_url` check):
  an unknown name raises `UnknownProviderPresetError` (listing the known names),
  and a key-requiring preset with no key set raises `MissingProviderKeyError` —
  so a misconfiguration surfaces as a clear config error here, not an opaque 401
  at call time. `requires_key=False` (Ollama) skips that guard.

### Why this exists alongside `factory.py`

`factory.build_router_from_settings` wires the *generic default slot* (one
hand-typed `base_url` + `api_key`) into a `ModelRouter`. This registry is the
complementary *by-name* path: it knows each preset's URL so the operator need not,
and it lets several providers' keys coexist. Both build the same adapter class;
neither owns routing policy. The registry is additive and does **not** modify
`factory.py`, `router.py`, or the policy — those stay the single control point for
role→model routing.

`build_provider` has **no caller yet** in this change (it is the operator-facing
seam). Wiring by-name selection into `build_router_from_settings` — e.g. a
`Settings.provider_preset` the factory resolves through this registry — is a
deferred follow-up that touches `factory.py`/`config.py` routing, deliberately out
of this change's scope.

## Consequences

### Positive

- An operator selects a known backend by name (`build_provider("groq", settings)`)
  with no URL to look up; multiple providers' keys coexist in one `.env`.
- Adding a backend is one registry entry (+ one `Settings` key if it needs auth) —
  no new adapter, honoring ADR 0007's "one adapter, many backends".
- Fully hermetic-testable: registry resolution, key wiring, the keyless-local
  path, and the unknown-name error need no network.

### Negative

- Per-preset `base_url` **override** is not exposed via config (it lives in the
  registry). This is deliberate (these URLs change rarely; adding four override
  fields is the speculative surface §7 warns against). If an override is ever
  needed it is a follow-up, not a reversal.
- The registry and `factory.py` both build `OpenAICompatibleProvider` from
  settings. The boundary is documented (above + in the module docstring) so the
  overlap reads as two intended entry points, not duplication.

### Neutral

- The NVIDIA and HuggingFace base URLs were reachability-checked (HTTP 200) at
  authoring; Groq/Ollama are the URLs already in `.env.example`. Per ADR 0007's
  precedent for the Groq model id, a preset is a documented default — correct it
  in this one file if a provider moves its endpoint.

## Alternatives considered

### Option A — only document the URLs in `.env.example` (no code)

Operator pastes the URL into the existing generic slot. **Pros:** zero code.
**Cons:** no by-name selection, keys still cannot coexist, the "which URL?" lookup
remains. **Why rejected:** the ergonomic gap is exactly what a small registry
removes cleanly.

### Option B — stringly-typed key attr (`getattr(settings, preset.key_attr)`)

**Pros:** marginally less code. **Cons:** not type-checked; a renamed `Settings`
field breaks silently at runtime. **Why rejected:** a `Callable[[Settings],
SecretStr]` is type-safe and refactor-safe for the same line count.

### Option C — per-preset `base_url` override fields in `Settings`

**Pros:** total runtime flexibility. **Cons:** four rarely-used config fields;
speculative surface (§7). **Why rejected:** the registry is the single, clear
place to change a URL; override is deferred until a real need appears.

## References

- Related: [ADR 0007](0007-openai-compatible-llm-adapter.md) (the one adapter this
  registry presets), [ADR 0003](0003-model-router-llm-fabric.md) (the fabric and
  policy this does not touch).
- [CLAUDE.md](../../CLAUDE.md) §6 (policy-driven routing), §4 (registry is a
  deterministic service/tool, not an agent), §7/§9 (minimal additive surface).
- [`backend/app/services/llm/providers.py`](../../backend/app/services/llm/providers.py),
  [`backend/.env.example`](../../backend/.env.example).
