# ADR 0047: NVIDIA NIM TTS Provider Adapter

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 introduced the Media Production layer as provider-neutral, fully-faked
seams of deterministic **tools** (CLAUDE.md §4 — never agents) and deferred the
concrete `TTSProvider` to a network-gated milestone behind the `tts/base.py`
protocol. ADR 0022 landed the first concrete one, the generic `HttpTtsProvider`.

This ADR lands a second concrete `TTSProvider` that targets the operator's
**existing NVIDIA build / NIM key** — the same key that already drives the LLM
path through the OpenAI-compatible adapter (ADR 0007/0028; build.nvidia.com's
hosted catalog speaks OpenAI-compatible JSON + `Authorization: Bearer`). Reusing
that proven credential for narration audio keeps the model fabric coherent
(CLAUDE.md §6) and avoids onboarding a separate TTS vendor.

Constraints match the LLM/search/`http_tts` adapters: the sandbox has HTTP
egress but no media vendor, and `httpx` is already a runtime dependency. So the
adapter is built on `httpx` and is fully offline-verifiable via
`httpx.MockTransport`; the live call is a `@pytest.mark.integration` smoke test,
skipped without credentials.

The discriminating constraint is the **fixed protocol seam**:
`async synthesize(*, text, voice) -> SynthesizedSpeech` is the swappable contract
(`FakeTTSProvider` satisfies it; an `isinstance(..., TTSProvider)` test enforces
it). The call args are only `text`/`voice` and the return is a
`SynthesizedSpeech` *descriptor* — so everything else a real synthesizer needs
(`model`, where the audio bytes go, how the clip duration is known) must be a
**construction-time dependency**, not a call argument or a new schema field.

## Decision

**Ship one `httpx` adapter over an NVIDIA TTS NIM speech endpoint:
`NvidiaTtsProvider` (`media/tts/nvidia.py`, `name = "nvidia"`),** selected by
`base_url` + `api_key` + `model` + the injected `sink` (the §6
provider-abstraction posture). It mirrors the LLM adapter's hardening and the
`http_tts` storage/duration design point-for-point.

- **Bytes → URI via an injected `sink`.** Per the `SynthesizedSpeech` docstring
  the descriptor is "not the audio bytes … an opaque blob **owned by storage**."
  So an `AudioSink = Callable[[bytes], str]` is **injected at construction**
  (mirroring the injected `client`): it persists the bytes and returns the
  `audio_uri`. Because the duration is probed from the *written file* (below),
  the sink must return a `file://` URI or a bare local path; the URI is resolved
  by the composition layer's `resolve_local_path` (reused, not reimplemented),
  which fails loud on a non-resolvable scheme.
- **Duration via `ffprobe` (no response field), fail-loud.**
  `SynthesizedSpeech.duration_ms` is required (`ge=0`) and a real NVIDIA speech
  endpoint returns **only raw audio bytes** — no duration header/field. So the
  duration is recovered from the rendered audio with `ffprobe` (the probe twin of
  the `ffmpeg` binary the composition layer already requires; ADR 0023), via the
  same **pure/impure split**: `build_ffprobe_args` + `parse_ffprobe_duration_ms`
  are pure and assertable with no binary; `_probe_duration_ms` is the single
  `subprocess.run` seam, run off the event loop via `asyncio.to_thread` and
  mockable in tests. A missing binary, non-zero exit, or unparseable duration
  raises `NvidiaTtsError` rather than a silently-wrong `0` (it drives downstream
  composition timing — the "video as long as its narration" rule).
- **Error boundary (mirrors ADR 0007/0022).** Operational failures (429, timeout,
  5xx via `raise_for_status`) propagate as raised `httpx` errors for the
  orchestrator to own; only a contract/probe failure is wrapped in a
  locally-defined `NvidiaTtsError`. The key never appears in a log, repr, or
  error message. Timeouts are bounded at construction (`60.0`s request,
  `30.0`s probe).

### Wire-contract assumption (verify on the first live call)

NVIDIA's speech NIM does **not** have a single firmly-documented hosted REST
shape: its native surfaces are gRPC / WebSocket, and the self-hosted NIM tutorial
exposes a multipart `POST /v1/audio/synthesize` form (`language`/`text`/`voice`)
returning raw WAV — neither of which carries a `model` field, while this task
requires `model` at construction and that we **mirror** the JSON/Bearer LLM and
`http_tts` adapters. Three constraints (the task's "endpoint/model at
construction", the repo's proven build.nvidia.com OpenAI-compatible JSON/Bearer
path, and the mirror requirement) all point the same way, so the adapter
**assumes** the OpenAI-compatible speech shape build.nvidia.com proxies:

- `POST {base_url}/audio/speech` with JSON body
  `{"model": ..., "input": <text>, "voice": ..., "response_format": ...}` and an
  `Authorization: Bearer {api_key}` header.
- Response body is the **raw audio bytes** in `response_format` (default `mp3`).

This is an **assumption to confirm on the first live call.** It is isolated so a
correction is a *small edit, not a rewrite*: the path is the `SPEECH_PATH`
constant and the body is built by a single pure `_build_payload` method, and the
integration smoke test parameterizes `model`/`voice`/`response_format` (and the
`base_url`) via env so the first run can confirm or adjust the shape (e.g. a
nested `input.text`, a `language_code`, or a fall-back to the multipart
`/v1/audio/synthesize` form) without code change. The sink, probe, and descriptor
mapping are unaffected by any such adjustment.

### Scope discipline

The adapter takes its `api_key`/`base_url`/`model` **at construction**, never from
global `Settings` — so `config.py` is untouched. `NvidiaTtsError` is defined
locally in `nvidia.py` (mirroring `OpenAICompatError`/`HttpTtsError`), not added
to `base.py`. `tts/base.py`, `media/__init__.py`, `main.py`, and `pyproject.toml`
are all untouched (no new dependency — `httpx`/Pydantic are runtime, `ffprobe` is
the already-required ffmpeg binary). The change is one new adapter module + its
hermetic test + a network-gated integration smoke. `NvidiaTtsProvider` is
reachable only via its full import path — factory/router wiring and a `Settings`
field follow once a media composition root selects a TTS provider by config.

## Alternatives considered

- **Reuse the generic `HttpTtsProvider` (ADR 0022).** Rejected: it requires an
  `X-Audio-Duration-Ms` response header a real NVIDIA endpoint does not send, and
  its body shape (`{text, voice}`, no `model`) does not match the NIM contract.
- **Probe nothing; trust a duration field.** Rejected: no such field exists on
  the raw-bytes response; a guessed `0` would silently corrupt composition
  timing.
- **Multipart `/v1/audio/synthesize` (self-hosted NIM) as the default.**
  Rejected as the *default* (no `model` field; conflicts with the mirror
  requirement) but documented as the first-class fall-back the isolated
  `_build_payload`/`SPEECH_PATH` seam can switch to after the live confirmation.
- **Add a `Settings` field + composition-root wiring now.** Deferred: out of the
  task's seam boundary, and wiring belongs with the media composition root.

## Consequences

- A second concrete `TTSProvider` reusing the operator's NVIDIA credential, fully
  hermetic-testable, behind the unchanged protocol seam.
- The hosted wire contract is an explicit, prominently-documented assumption with
  a cheap confirm/adjust path (isolated constant + `_build_payload` + env-driven
  smoke) — the first live call closes it.
- No production wiring yet (capability-before-wiring, the M-LP pattern); a
  `Settings` field + provider selection in the media composition root is the
  documented follow-up.
