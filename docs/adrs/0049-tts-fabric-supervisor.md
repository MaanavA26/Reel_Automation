# ADR 0049: Agent-supervised TTS fabric (router + supervisor)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Media Production layer (CLAUDE.md §3.3) will soon have several concrete
`TTSProvider` backends behind the existing `app.media.tts.base.TTSProvider`
protocol — a local Kokoro adapter, an NVIDIA adapter, a HuggingFace adapter, and
the generic HTTP adapter (`http_tts.py`). They are being built on sibling
branches. Two problems then appear:

1. **Resilience.** Backends differ in availability and cost. A render should not
   hard-fail because the chosen backend is momentarily down — it should produce
   *some* audio if *any* backend can.
2. **Selection.** *Which* backend + voice best renders a given narration beat —
   given an optional channel voice/tone hint — is a quality call, not a
   deterministic transform.

These are the same two concerns the LLM model fabric already solved, and the
project's standing rule (CLAUDE.md §4/§6) is to keep them apart: policy-driven
routing + mechanical fallback is a **tool/service**; choosing is **judgment** and
belongs to an **agent**. We must not "make everything an agent" (CLAUDE.md §11
bad patterns), and we must not introduce uncontrolled multi-model/backend chatter
(§6).

## Decision

Ship two cleanly separated units, mirroring the LLM fabric's router/resilience
and the §11 index-then-validate pattern.

### 1. `TTSRouter` — the deterministic fabric (a tool)

`backend/app/media/tts/router.py`. Holds named `TTSProvider`s and an ordered
`fallback_order` policy (cheapest/most-local first, e.g. kokoro → nvidia →
huggingface). `synthesize(*, text, voice, backend=None)` tries the chosen backend
(or `default_backend`) and, on failure, walks the rest of the order until one
succeeds, raising `TTSExhaustedError` (chaining the last provider error) only when
**all** fail. Construction validates the policy (non-empty; every name
registered). Pure, hermetic, no network/audio of its own.

This mirrors `app.services.llm.resilience` but with one deliberate divergence:
`complete_with_fallback` does exactly **one** hop (primary → `FALLBACK` role); the
TTS router does a **full ordered traversal**, because a media render should
degrade to a working backend rather than fail. The chosen backend is removed from
its policy position so it is never tried twice — a mid-order pick still gets the
cheaper earlier backends as a safety net.

### 2. `TTSSupervisorAgent` — the selector (an agent)

`backend/app/agents/tts_supervisor.py`. Given the beat text and an optional
channel voice/tone hint, it asks the `PLANNING`-role model (via `ModelRouter`) to
choose a backend + voice, returning a transient `_SupervisorChoice` DTO (backend,
voice, rationale). It owns no provider code and synthesizes nothing itself.

Both halves of the §11 pattern are enforced so the agent is not theater:

- **Index:** the router's *real* `available()` backend set is listed in the
  prompt — the model picks from genuine options, not blind.
- **Validate/clamp:** the response backend is validated against that same set; an
  out-of-set pick is **clamped to the router default** (recorded as
  `TTSDecision.clamped=True` for provenance).

The agent then calls `tts_router.synthesize(...)`, whose deterministic fallback
guarantees audio. The public method returns `SupervisedSpeech` (the produced
`SynthesizedSpeech` + the validated `TTSDecision`). **Agent proposes, router
disposes + guarantees delivery.**

## Consequences

- The agent-vs-tool seam is structural and visible: the constructor takes a
  named `model_router` (judgment) and `tts_router` (execution).
- Output is always guaranteed: an invalid model pick clamps to the default, and
  even a valid-but-failing backend falls through to a working one.
- Both units are fully hermetic against `FakeTTSProvider` + `FakeProvider` (one
  fake made to fail drives the fallback assertions); no concrete adapter is
  imported — they bind to the `TTSProvider` protocol, so the sibling adapter
  branches drop in unchanged.
- **Scope/deferral:** this is capability, not wiring. Registering the concrete
  backends and threading the supervisor into the `MediaPipeline`/video pipeline
  is a follow-up; `config.py`, `main.py`, `__init__.py`, `base.py`,
  `composition.py`, and the adapter files are untouched. Tone is consumed as a
  prompt *hint* (it informs voice choice); it is not an output field, matching
  the `TTSProvider.synthesize(*, text, voice)` contract.
