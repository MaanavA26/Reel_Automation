# ADR 0022: HTTP TTS Provider Adapter (first concrete `TTSProvider`)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 introduced the Media Production layer as provider-neutral, fully-faked
seams of deterministic **tools** (CLAUDE.md §4 — never agents) and explicitly
deferred the concrete `TTSProvider` to a network-gated milestone behind the
`tts/base.py` protocol. This ADR lands the first concrete one,
`HttpTtsProvider`, the media-layer analogue of the M-LP.1 LLM adapter
(ADR 0007) and the M-LP.2 search adapters (ADR 0013/0021).

Constraints are identical to those adapters: the sandbox has HTTP egress but no
`pip` index and no media vendor, and `httpx` is already a runtime dependency. So
the adapter is built on `httpx` and is fully offline-verifiable via
`httpx.MockTransport`; the live call is a `@pytest.mark.integration` smoke test,
skipped without credentials.

The discriminating constraint is the **fixed protocol seam**:
`async synthesize(*, text, voice) -> SynthesizedSpeech` is the swappable contract
(`FakeTTSProvider` satisfies it; an `isinstance(..., TTSProvider)` test enforces
it). The call args are only `text`/`voice` and the return is a
`SynthesizedSpeech` *descriptor* — so everything else a real synthesizer needs
(where the audio bytes go, how the clip duration is known) must be a
**construction-time dependency**, not a call argument or a new schema field.

## Decision

**Ship one `httpx` adapter over a generic REST TTS endpoint:
`HttpTtsProvider` (`media/tts/http_tts.py`, `name = "http"`).** "Generic REST"
(not a named vendor) is deliberate — one adapter serves any backend exposing the
contract below, selected by `base_url` + `api_key` + the injected `sink` (the
§6 provider-abstraction posture; a named-vendor variant is a later, trivial
subclass of this contract).

- **Wire contract.** `POST {base_url}/synthesize` with JSON body
  `{"text": ..., "voice": ...}` and an `Authorization: Bearer {api_key}` header.
  The response body is the **raw audio bytes** (text in, audio out — the
  deterministic-tool shape of §4).
- **Bytes → URI via an injected `sink` (the key design choice).** The
  `SynthesizedSpeech` docstring is explicit: it is "a lightweight descriptor,
  **not the audio bytes** … the bytes themselves are an opaque blob **owned by
  storage**." So the adapter must *not* embed bytes (a `data:` URI contradicts
  that intent) and must *not* choose where audio lives (that couples it to
  storage). Instead an `AudioSink = Callable[[bytes], str]` is **injected at
  construction** (mirroring the injected `client`): it persists the bytes and
  returns the `audio_uri` to record. Tests inject a capturing in-memory sink; a
  real deployment injects an object-store / filesystem sink. The alias lives in
  the adapter module (not `schemas.py`, not a new storage module) to avoid
  abstraction sprawl (§7).
- **Duration from a response header, fail-loud on absence.**
  `SynthesizedSpeech.duration_ms` is required (`ge=0`) and cannot be recovered
  from opaque audio bytes without a format-specific parser (stdlib `wave` is
  WAV-only — not provider-neutral; adding a decoder adds a dependency). So the
  contract carries it in an `X-Audio-Duration-Ms` integer-millisecond response
  header. A missing / non-integer / negative header raises `HttpTtsError` rather
  than silently propagating a wrong `0` into downstream composition timing.
- **Error boundary (mirrors ADR 0007/0013/0021).** Operational failures (429,
  timeout, 5xx via `raise_for_status`) propagate as raised `httpx` errors for the
  orchestrator to own (retries/budgets are its concern); only a
  contract-violating *response shape* (the bad duration header) is wrapped in a
  locally-defined `HttpTtsError`. The key never appears in a log, repr, or error
  message. Timeout is bounded at construction (default `60.0`s).

### Scope discipline

The adapter takes its `api_key` **at construction**, never from global
`Settings` — so `config.py` is untouched (matching the task's seam boundary, and
mirroring how `BraveSearchProvider` could be constructed standalone). `HttpTtsError`
is defined locally in `http_tts.py` (mirroring `OpenAICompatError` /
`SearchError`), not added to `base.py`; `tts/base.py`, `media/__init__.py`,
`main.py`, and `pyproject.toml` are all untouched. The change is one new adapter
module + its tests. Consequently `HttpTtsProvider` is reachable only via its full
import path — factory/router wiring and a `Settings` field are out of scope (and
follow once a media composition root exists).

## Consequences

### Positive

- A real `TTSProvider` exists behind the ADR 0019 seam; the future media pipeline
  can synthesize narration by construction (config swap once wiring lands).
- Provenance integrity holds — the artifact records `produced_via = "tts:http"`,
  symmetric with the fake's `"tts:fake"` (ADR 0019).
- The storage-neutral `sink` keeps the layer's descriptor-not-bytes invariant
  (ADR 0019) intact: the adapter never decides where audio lives.
- Fully offline-verifiable: endpoint, auth header, request body, the
  bytes→sink→`audio_uri` mapping, and the duration-header contract (present /
  missing / non-integer / negative) are all unit-tested via `httpx.MockTransport`
  + a capturing fake sink.

### Negative

- **Live behavior is unverifiable offline.** Whether a real endpoint matches the
  asserted contract (the duration header in particular) can only be confirmed by
  the `@pytest.mark.integration` smoke test against a real endpoint; the hermetic
  fixtures pin the contract as designed.
- **The duration-header contract is an assumption** about the generic endpoint.
  A vendor that returns duration in a JSON envelope instead would need a sibling
  adapter; this one fails loud rather than guessing, which is the intended
  trade-off.

### Neutral

- No schema change (`SynthesizedSpeech` already exists). No new dependency
  (`httpx` is already runtime). No wiring change.

## Deferred (with reasons)

- **Factory wiring / a `Settings` field / a media composition root** — follows a
  consumer; out of this adapter's scope (and would touch `config.py`).
- **Named-vendor adapters** (ElevenLabs/Azure with their own envelopes/headers)
  — a later subclass-or-sibling per vendor, behind this same protocol, when a
  specific vendor is chosen.
- **A duration-from-bytes decoder** (drop the header dependency) — needs an audio
  parser dependency; added only if a target endpoint cannot supply the header.
- **A streaming / chunked-audio variant** — the protocol returns a single
  descriptor; streaming earns its own seam when a consumer needs it (§7/§13).

## Alternatives considered

### Option A — Embed the audio bytes in the descriptor (e.g. a `data:` URI)

**Pros:** no injected dependency. **Cons:** directly contradicts the
`SynthesizedSpeech` docstring ("**not the audio bytes** … owned by storage") and
ADR 0019's descriptor-not-bytes invariant; bloats the DTO. **Why rejected:** the
seam's whole point is storage-neutral descriptors.

### Option B — Let the adapter write to a path / pick storage itself

**Pros:** self-contained. **Cons:** couples the adapter to a storage choice,
breaking provider-neutrality and making it untestable without a filesystem. **Why
rejected:** injecting a `sink` (mirroring the injected `client`) is the same
dependency-injection posture the rest of the repo uses.

### Option C — Default `duration_ms` to `0` when the header is absent

**Pros:** never raises. **Cons:** silently feeds a wrong timing into downstream
composition — exactly the kind of hidden-wrong value §11/§7 warn against. **Why
rejected:** fail loud; duration is load-bearing for composition.

## References

- Related: [ADR 0019](0019-media-production-layer.md) (the media seam this fills;
  `TTSProvider` protocol, `SynthesizedSpeech` descriptor, `produced_via`
  provenance, concrete-adapter deferral), [ADR 0007](0007-openai-compatible-llm-adapter.md)
  (the httpx + `MockTransport` hardening pattern; operational-vs-shape error
  boundary; key-at-construction), [ADR 0013](0013-live-search-adapter.md) /
  [ADR 0021](0021-brave-search-adapter.md) (sibling concrete adapters mirroring
  the same posture).
- [CLAUDE.md](../../CLAUDE.md) §3.3 (media layer), §4 (agent-vs-tool; TTS is a
  tool), §6 (provider abstraction), §7/§13 (no speculative overbuild), §9 (scope
  discipline), §11 (no hidden-wrong values).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer (concrete adapters).
