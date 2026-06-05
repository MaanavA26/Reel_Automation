# ADR 0048: HuggingFace TTS Provider Adapter (second concrete `TTSProvider`)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 introduced the Media Production layer as provider-neutral, fully-faked
seams of deterministic **tools** (CLAUDE.md §4 — never agents) and deferred the
concrete `TTSProvider` behind the `tts/base.py` protocol. ADR 0022 landed the
first one, `HttpTtsProvider`, over a *generic* REST contract. This ADR lands a
second, `HuggingFaceTtsProvider`, over the **HuggingFace serverless Inference
API** — so narration can run off the operator's **existing `hf_` key** (already
present for the LLM provider registry, ADR 0028) with no new vendor account.

Constraints match the other adapters: the sandbox has HTTP egress but no media
vendor, and `httpx` is already a runtime dependency. So the adapter is built on
`httpx` and is fully offline-verifiable via `httpx.MockTransport`; the live call
is a `@pytest.mark.integration` smoke test, skipped without credentials.

The discriminating constraint is the **fixed protocol seam**:
`async synthesize(*, text, voice) -> SynthesizedSpeech`. The call args are only
`text`/`voice` and the return is a *descriptor* — so everything else a real
synthesizer needs (where the audio bytes go, how the clip duration is known) must
be a **construction-time dependency**, not a call argument or schema field. The
HuggingFace surface adds three wrinkles the generic adapter did not face: the TTS
task takes a bare `inputs` string with **no voice selector**; a serverless model
that is asleep returns a **cold-start 503** with an `estimated_time`; and the
response carries **no duration** (and HF can return a JSON error with `200`).

## Decision

**Ship one `httpx` adapter over the HuggingFace Inference API:
`HuggingFaceTtsProvider` (`media/tts/huggingface.py`, `name = "huggingface"`),
selected by `model` + an `hf_` `token` + the injected `sink` + an overridable
`api_root`.**

- **Wire contract.** `POST {api_root}/models/{model}` with an
  `Authorization: Bearer {token}` header and the JSON body `{"inputs": text}`.
  The success response body is the **raw audio bytes** (FLAC/WAV per model).
- **Model-is-the-voice (the `voice`-arg decision).** The HF TTS task has no
  generic voice parameter — the *model* is the voice, chosen at construction. So
  `voice` is **echoed into `SynthesizedSpeech.voice` for provenance but never
  sent**; per-model speaker params are a later extension. This is the same
  construction-time-dependency-vs-call-arg posture ADR 0022 took for the sink.
- **Cold-start 503 → raise, do not sleep (the key resilience decision).** A
  not-loaded serverless model returns `503` + a JSON `estimated_time`. The
  adapter inspects the 503 body **before** `raise_for_status` and raises a typed
  `HuggingFaceTtsError(estimated_time=…)` so the **orchestrator owns the retry**
  (retries/budgets are its concern — ADR 0022/0027; the scheduler/resilience
  layers avoid in-process sleeping). HF's native `options.wait_for_model=true`
  flag is an alternative (Option C below); explicit 503 handling keeps the
  adapter time-decoupled and consistent with the repo's "errors propagate"
  boundary.
- **Duration from `ffprobe` over the bytes.** `SynthesizedSpeech.duration_ms` is
  required (`ge=0`) and HF returns no duration. It is computed by piping the
  audio **bytes** through `ffprobe -i pipe:0`
  (`-show_entries format=duration`). Probing the *bytes* — not a file path —
  keeps duration **independent of the injected `sink`** (an in-memory or
  object-store sink, which has no local file, still works). The `ffprobe` call
  is the single mockable exec seam (`_probe_duration_ms`), run off the event loop
  via `asyncio.to_thread`, mirroring `FfmpegCompositionService._run` (ADR 0023):
  a missing binary, a **timeout** (bounded by the same construction `timeout`),
  a non-zero exit, or an unparseable duration all normalize to one
  `HuggingFaceTtsError`.
- **Reuse the storage seam.** The `AudioSink = Callable[[bytes], str]` alias is
  **imported from `http_tts`**, not redefined — one media-layer storage seam (§7,
  no abstraction sprawl). The adapter never embeds bytes or chooses storage.
- **Error boundary (mirrors ADR 0007/0022).** Generic operational failures
  (401/timeout/5xx via `raise_for_status`) propagate as raised `httpx` errors;
  only the HF-specific shapes are wrapped in `HuggingFaceTtsError` — the
  cold-start 503, a JSON error envelope returned with `200` (caught by a
  content-type guard before the bytes are treated as audio), and a probe
  failure. Upstream-body excerpts are length-bounded (`_ERR_BODY_MAX`,
  mirroring ADR 0043) and the `hf_` token never appears in a repr/log/error.
  Timeout is bounded at construction (default `60.0`s).

### Scope discipline

`model`/`token` are taken **at construction**, never from global `Settings` — so
`config.py` is untouched (matching the task's seam boundary and ADR 0022). The
change is one new adapter module + its hermetic test + a live integration smoke;
`__init__.py`, `base.py`, `config.py`, `composition.py`, and `pyproject.toml` are
all untouched. Consequently `HuggingFaceTtsProvider` is reachable only via its
full import path — factory/router wiring and a `Settings` field follow once a
media composition root selects it (the same deferral ADR 0022 made).

## Consequences

### Positive

- A second real `TTSProvider` exists behind the ADR 0019 seam, driven by the
  operator's existing `hf_` key (no new account/cost to start).
- Provenance integrity holds — the artifact records `produced_via =
  "tts:huggingface"`, symmetric with `"tts:http"` / `"tts:fake"`.
- Duration is sink-independent: probing the bytes means an in-memory test sink
  and a filesystem/object-store production sink both work unchanged.
- Fully offline-verifiable: endpoint, auth header, request body, cold-start
  handling (with/without estimate), the non-audio-body guard, the
  bytes→sink→`audio_uri` mapping, and every `ffprobe`-seam branch are unit-tested
  via `httpx.MockTransport` + a capturing fake sink + a monkeypatched seam.

### Negative

- **A second binary dependency (`ffprobe`) at run time** — unlike `HttpTtsProvider`
  (which reads a header). It is the standard FFmpeg companion (already implied by
  the composition layer, ADR 0023) and the only portable, provider-neutral way to
  recover duration from opaque audio bytes; the integration smoke skips when it is
  absent.
- **Live behavior is unverifiable offline.** Whether a given live model matches
  the asserted contract (audio content-type, cold-start shape) can only be
  confirmed by the `@pytest.mark.integration` smoke; the hermetic fixtures pin
  the contract as designed.
- **Serverless cold-starts / rate limits.** Fine for testing and low volume; the
  first call to a sleeping model raises (the orchestrator retries). Production
  scale points `api_root` at a warm paid Inference Endpoint — a config change,
  no code change.

### Neutral

- No schema change (`SynthesizedSpeech` already exists). No new Python dependency
  (`httpx` is already runtime; `ffprobe` is a binary, not a package). No wiring
  change.

## Deferred (with reasons)

- **Factory wiring / a `Settings` field / a media composition root** — follows a
  consumer; out of this adapter's scope (and would touch `config.py`).
- **Per-model speaker / voice parameters** — the bare `{"inputs": text}` body is
  the documented HF TTS task shape; model-specific `parameters` are added when a
  target model needs them.
- **A `wait_for_model` bounded-wait mode** — an injected-sleeper variant (ADR
  0027 posture) is a trivial later add if a caller prefers waiting to retrying.

## Alternatives considered

### Option A — ffprobe a file path written by the sink

**Pros:** simpler arg list. **Cons:** couples duration to "the sink wrote a
locally-readable file" — a `mem://`/object-store sink would have no path to probe
and the hermetic test would need a tmp-file sink. **Why rejected:** probing the
bytes via `pipe:0` keeps duration sink-independent.

### Option B — Send `voice` in the request body

**Pros:** uniform with `HttpTtsProvider`. **Cons:** the HF TTS task has no voice
field; sending one is ignored or errors. **Why rejected:** the model *is* the
voice (construction-time), echoed for provenance.

### Option C — `options.wait_for_model=true` (let HF block until loaded)

**Pros:** no cold-start error. **Cons:** moves the wait inside the request
(coupling latency/timeout to model load) and hides the retry decision from the
orchestrator that owns budgets. **Why rejected:** explicit 503-raise keeps the
adapter time-decoupled, consistent with ADR 0022/0027; noted as a future mode.

## References

- Related: [ADR 0022](0022-tts-adapter.md) (sibling concrete `TTSProvider`; the
  injected-`sink` storage seam reused here, key-at-construction, operational-vs-
  shape error boundary), [ADR 0019](0019-media-production-layer.md) (the media
  seam; `TTSProvider` protocol, `SynthesizedSpeech` descriptor, `produced_via`
  provenance), [ADR 0023](0023-ffmpeg-composition.md) (the pure/exec-seam split +
  subprocess error normalization the `ffprobe` seam mirrors),
  [ADR 0028](0028-provider-registry.md) (the existing `hf_` HuggingFace key this
  reuses), [ADR 0027](0027-llm-resilience.md) (retries/sleeping are the
  orchestrator's concern), [ADR 0043](0043-fetch-and-render-hardening.md)
  (bounded error-body excerpts).
- [CLAUDE.md](../../CLAUDE.md) §3.3 (media TTS), §4 (agent-vs-tool; TTS is a
  tool), §6 (provider abstraction), §7/§13 (no speculative overbuild), §9 (scope
  discipline), §11 (no hidden-wrong values).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer (concrete adapters).
