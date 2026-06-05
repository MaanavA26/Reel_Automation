# ADR 0026: LLM Response Cache (Model-Fabric Decorator)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Deep Research pipeline issues many LLM calls, and several are *repeated
verbatim* within a single run or across re-runs: the same extraction prompt over
a re-fetched chunk, an identical planning call during an iterated dev loop, a
fan-out that revisits the same prompt. Each repeat costs latency and money for an
answer the system has already seen.

The model fabric (ADR 0003) gives us the seam to fix this without touching any
caller: every provider implements one `ModelProvider.complete_structured(*, model,
system, prompt, schema)` contract. A cache can live entirely at that boundary.

Per CLAUDE.md §4 this is **service/tool** work — a deterministic, judgment-free
performance wrapper — not an agent. The open questions were narrow: (1) how to key
a cache entry stably and collision-free, (2) how to keep the storage pluggable
without over-building, and (3) how to be honest about the correctness trade a
cache imposes on a non-deterministic model.

## Decision

**We add `CachingModelProvider` in `app/services/llm/cache.py` — a decorator that
*wraps* any `ModelProvider` (composition, not inheritance) and memoizes
`complete_structured` results.** It is opt-in: nothing wraps a provider by default;
a caller composes it explicitly where the trade is acceptable.

1. **Cache key** — a SHA-256 over canonical JSON of
   `(model, system, prompt, schema-identity)`. Schema identity is *both* the
   fully-qualified class name **and** the full JSON Schema (`model_json_schema()`),
   so two distinct schemas with identical fields — and any change to a schema's
   shape — produce distinct keys and never collide. `sort_keys` makes the
   serialization order-independent. Stdlib only (`hashlib` + `json`).
2. **Pluggable storage** — a tiny `CacheBackend` protocol (`get`/`set`) with an
   in-memory default, `InMemoryCacheBackend`, backed by a stdlib `OrderedDict`.
   `max_size=None` is unbounded; a positive `max_size` makes it an LRU
   (`move_to_end` on access, `popitem(last=False)` on overflow). A persistent or
   shared backend (Redis, disk) can be dropped in later behind the same protocol.
3. **Hit/miss semantics** — a hit returns the cached value *without touching the
   wrapped provider*; a miss calls through and populates. Returned values are
   deep-copied (`model_copy`) on store and on return, so a caller mutating a
   result cannot corrupt the cached entry or a later hit.
4. **Exceptions are never cached.** A failed wrapped call propagates and leaves the
   key absent, so the next identical call retries (no negative caching).

### Scope boundary

This PR ships only `cache.py` + hermetic tests. It does **not** register the cache
in the router, alter `default_policy`, or wrap any provider — wiring is a separate,
deliberate composition-root change once a consumer wants it. No config, no new
dependency (stdlib only).

## Consequences

### Positive

- Repeated identical calls cost one provider round-trip instead of N — a direct
  latency/$ saving on the fan-out- and replay-heavy research pipeline.
- The fabric stays provider-neutral: the decorator composes around the ADR 0003
  contract and works for *any* provider (OpenAI-compatible, Gemini, fakes) with
  zero caller changes.
- Fully hermetic and deterministic: tests wrap a `FakeProvider` and assert the
  underlying provider is called once across two identical requests, not at all on
  the second — and that a broken cache fails loudly (single-seeded fake exhausts).

### Negative

- **Freshness trade (the core caveat).** Caching assumes the wrapped model is
  effectively deterministic for a given input. Real LLMs are not — even at
  `temperature=0` output can vary. So two identical requests return the *first*
  response, not a fresh sample. This is acceptable only where freshness is
  expendable; hence opt-in, documented in the module docstring, and never on by
  default.
- **No cross-process / persistent default.** The in-memory backend is process-local
  and lost on restart. A shared/persistent backend is a future plug-in, not built
  now (avoids speculative surface, CLAUDE.md §7).

### Neutral

- **Concurrency stampede is a deliberate non-goal.** Two concurrent identical
  *misses* both call the wrapped provider — there is no in-flight de-duplication
  lock. For the cache's cost-saving intent this is fine; building stampede control
  now would be speculative surface. Named here so a reviewer reads it as a decision,
  not an oversight.

## Alternatives considered

### Option A — Inherit from a provider / mixin

Subclass each provider to add caching. **Cons:** couples the cache to each concrete
provider, multiplies classes, and breaks the provider-neutral seam. **Rejected:**
the decorator composes around the one shared contract instead — one class, every
provider.

### Option B — Key on the prompt only (ignore schema)

Hash `(model, system, prompt)`. **Cons:** two callers requesting different schemas
for the same prompt would collide, returning a value of the wrong type. **Rejected:**
schema identity is part of what makes a response correct, so it belongs in the key.

### Option C — Bake the cache into the router / `default_policy`

Wrap every resolved provider automatically. **Cons:** makes a freshness-sacrificing
behavior the silent default for every agent, violating the opt-in principle the
non-determinism caveat demands. **Rejected:** the cache is composed explicitly by a
caller that has decided the trade is acceptable.

## References

- Related: [ADR 0003 — Model Router and LLM Fabric](0003-model-router-llm-fabric.md)
  (the `ModelProvider` contract this decorator wraps).
- Related: [ADR 0007 — OpenAI-Compatible LLM Adapter](0007-openai-compatible-llm-adapter.md)
  and [ADR 0020 — Gemini-Native Adapter](0020-gemini-native-adapter.md)
  (concrete providers the cache composes around unchanged).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §6 (model fabric), §7 (no
  speculative overbuild), §9 (scope discipline).
