# ADR 0050: Wiring the supervised TTS fabric — local-first (Kokoro) default

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The TTS *capability* shipped piecewise: the deterministic `TTSRouter` and the
judgment-bearing `TTSSupervisorAgent` (ADR 0049), and four concrete `TTSProvider`
adapters — local Kokoro (ADR 0046), NVIDIA (0047), HuggingFace (0048), OpenAI
(0045). ADR 0049 deferred *wiring*: `config.py`, `composition.py`, `doctor.py`,
and `.env.example` were untouched, and `build_media_deps` still hard-wired the
generic `HttpTtsProvider`, hard-failing unless `REEL_AUTOMATION_TTS_BASE_URL` +
`REEL_AUTOMATION_TTS_API_KEY` were set.

That contradicts the project's intent for a high-volume faceless-shorts engine:
narration should default to a **local, zero-cost** backend (CLAUDE.md §6
local-model role), so a first video needs *no TTS service account*. This ADR is
the keystone integration that makes the end-to-end pipeline produce a video with
a fully local voice (Kokoro) and no TTS service.

## Decision

### 1. Config — local-default, additive (`config.py`)

`tts_backend: str = "kokoro"` selects which backend the **doctor** checks for
readiness — it is *not* read by the live wiring (the supervisor chooses per beat
among all wired backends; the router's policy head, always Kokoro, is the default
and the fallback). Kokoro needs no key — only the two model files, whose paths default to the
canonical filenames (`kokoro_model_path="kokoro-v1.0.onnx"`,
`kokoro_voices_path="voices-v1.0.bin"`) so the provider **constructs**
unconditionally (the files are read lazily at synth time). `tts_voice` now
defaults to a Kokoro voice (`af_heart`). NVIDIA/HuggingFace fallback fields
(`*_tts_base_url`/`*_tts_model`/`*_tts_api_key`, keys as `SecretStr`) are added;
their base_url/model carry non-empty defaults so the adapters construct when
their key is set. The old `tts_base_url`/`tts_api_key` (the generic
`HttpTtsProvider`) are **removed** — they had no remaining consumer.

### 2. `build_media_deps` — a supervised router (`composition.py`)

The TTS seam the `MediaPipeline` consumes is now assembled as a router + agent:

- **Kokoro is always registered** (local, no key) — this is what lets a
  Kokoro-only setup build with no service key. NVIDIA/HuggingFace join the
  `TTSRouter` **only when their key is set**, in cheapest-first policy order
  (`kokoro → nvidia → huggingface`); a missing fallback key is silent, never a
  `CompositionError`.
- The router is wrapped in a `TTSSupervisorAgent` (built over a `ModelRouter`)
  and exposed to the pipeline through a new thin adapter,
  `app.media.tts.supervised.SupervisedTtsProvider`, which satisfies the
  structural `TTSProvider` protocol (`synthesize(*, text, voice) ->
  SynthesizedSpeech`) by forwarding `voice` as the supervisor's `voice_hint` and
  unwrapping `SupervisedSpeech.speech`. **The pipeline's `tts` contract is
  unchanged** — no edit to `MediaPipeline`, the router, the supervisor, or any
  adapter.

### 3. Closables without touching the adapters

The NVIDIA/HuggingFace/OpenAI adapters own an `httpx.AsyncClient` but expose no
`aclose` (only `HttpTtsProvider` is a `CloseOwnedClientMixin`). Rather than
modify them, the composition root **owns the client**: it constructs the
`httpx.AsyncClient` (60s timeout, matching the adapters' default), injects it via
each adapter's `client=` parameter, and registers *the client itself* — which
satisfies `AsyncClosable` — in the `MediaBundle.closables`. Kokoro owns no client
and is not a closable. The lifespan drains these exactly as before (ADR 0044).

### 4. Doctor — backend-aware, offline (`doctor.py`)

`_check_tts` branches on `tts_backend`: for `kokoro`,
`importlib.util.find_spec("kokoro_onnx")` (no import executed) **and** a file
stat of the model/voices paths, with the exact `pip install kokoro-onnx` +
download hint when missing (it never downloads — it detects and instructs); for
`nvidia`/`huggingface`, the backend's key. Still no network, no paid call.

### 5. `.env.example` — local-Kokoro by default

The committed template defaults to a no-TTS-service Kokoro setup (the
`pip install` + model-download steps, the paths), with NVIDIA/HF as commented
optional fallbacks.

## Consequences

- **A Kokoro-only setup (no NVIDIA/HF/OpenAI key) builds and renders.** Verified:
  `build_video_pipeline(Settings(tts_backend="kokoro", <llm+search+stock set>, no
  TTS key))` constructs without raising (the keystone test).
- **Two model clients on the live end-to-end path.** The TTS supervisor needs a
  `ModelRouter`, so `build_media_deps` mints its own (independent of
  `build_research_deps`'). This is a deliberate trade for the smallest blast
  radius — `build_research_deps` and its tests stay untouched — at the cost of
  one extra LLM `httpx.AsyncClient` (both are closed on shutdown). A future
  refactor could thread one shared router through `build_video_pipeline`.
- **OpenAI TTS left wire-ready, unwired.** The router covers
  kokoro/nvidia/huggingface; adding the OpenAI-TTS adapter is a small follow-up
  (a `Settings` key field + one router branch), deferred to avoid overbuild (§7).
- **Known live-behavior caveat (out of scope).** The supervisor's validation
  clamps only the *backend*, not the voice — the model's chosen voice always
  wins. With a Kokoro-only router there is no fallback for a bad voice id, so an
  invalid voice could fail at synth time. This is frozen ADR 0049 behavior; the
  `tts_voice` default is only a hint.
- The agent-vs-tool boundary stays intact and visible (CLAUDE.md §4): the
  supervisor proposes, the router disposes + guarantees delivery, and the
  pipeline still sees one deterministic `TTSProvider`.
