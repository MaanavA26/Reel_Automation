# ADR 0045: OpenAI `/audio/speech` TTS adapter (ffprobe-derived duration)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Media Production layer's TTS seam (ADR 0019 protocol, ADR 0022 first adapter)
ships `HttpTtsProvider` against a *generic* REST contract: `POST /synthesize`
returning raw audio bytes **plus** an `X-Audio-Duration-Ms` response header that
populates the required `SynthesizedSpeech.duration_ms`.

That header does not exist in the real world. OpenAI's `/v1/audio/speech` (and
every OpenAI-compatible TTS backend) returns **only the raw audio bytes** — no
duration metadata. So `HttpTtsProvider` fails against any standard provider: it
raises `HttpTtsError("missing required 'X-Audio-Duration-Ms' header")` on the
first real call. This blocks the **first end-to-end video**, which needs real
narration with a real duration (the composition step's master clock — the "video
is as long as its narration" rule, ADR 0023).

`duration_ms` cannot be silently defaulted to `0` (it drives composition timing)
and cannot be recovered from opaque audio bytes without parsing the container.
The binary that *can* read it — `ffprobe` — already ships with `ffmpeg`, which
the render step already requires. So the duration is recoverable with no new
dependency.

## Decision

Add a concrete **`OpenAiTtsProvider`** (`backend/app/media/tts/openai_tts.py`)
beside `HttpTtsProvider`, not a rewrite of it — the generic header contract is
still valid for a backend that *does* send the header; this is a second adapter
behind the same `TTSProvider` protocol (the twice-blessed fabric pattern).

**Speaks the real OpenAI `/audio/speech` contract.** `POST {base_url}/audio/speech`,
`Authorization: Bearer {api_key}`, JSON body `{"model", "input", "voice",
"response_format"}`. The protocol's `synthesize(*, text, voice)` carries only
text + voice (the deterministic-tool contract, CLAUDE.md §4), so `model`
(**required** by OpenAI) and `response_format` are **constructor params**
(defaults `"tts-1"` / `"mp3"`). The text field is `input`, not `http_tts`'s
`text`. Provider-neutral: any OpenAI-compatible `/audio/speech` backend works by
`base_url` + `api_key`.

**Hardened like the LLM adapter** (`openai_compatible`, ADR 0007/0021/0022):
bounded timeout, API key passed at construction (never `Settings` — stays out of
`config.py`), injectable `httpx.AsyncClient`, `raise_for_status` so operational
failures surface as native `httpx` errors.

**Duration from the rendered audio via `ffprobe`, with the pure/impure split
mirroring `ffmpeg.py` (ADR 0023):**

- `build_ffprobe_args(path) -> list[str]` — pure command construction
  (`ffprobe -v error -show_entries format=duration -of json <path>`); argv
  token-assertable with **no binary present**.
- `parse_ffprobe_duration_ms(stdout) -> int` — pure parse of the JSON
  `format.duration` (seconds) → `round(seconds * 1000)`; raises on `N/A` /
  missing key / non-numeric / negative (never a silently-wrong `0`).
- `_probe_duration_ms(path)` — the **single** `subprocess.run` seam, run off the
  event loop via `asyncio.to_thread`, a mockable point in tests. A missing binary
  (`FileNotFoundError`), timeout, and non-zero exit all normalize to one
  `OpenAiTtsError` carrying the `shlex.join`'d command (argv list, never a shell
  — no injection surface), symmetric with `CompositionError`.

**Probe the *written file*, not a pipe.** `synthesize` persists the bytes through
the injected `sink` (the storage seam, mirroring `http_tts`), then probes the
file the sink wrote. This honors the descriptor-not-bytes invariant and matches
the "ffprobe on the written file" requirement. The sink's `audio_uri` is resolved
to a local `Path` by **reusing `resolve_local_path`** from the composition
adapter (a read-only import — the thumbnail renderer set this precedent), which
fails loud on a non-resolvable scheme. The resulting constraint: this provider
requires a sink returning a `file://` URI or bare path — which the local render
pipeline already requires of its audio.

## Consequences

### Positive

- The first real video is unblocked: a standard OpenAI(-compatible) TTS endpoint
  now yields a `SynthesizedSpeech` with a *real* duration, out-of-box.
- No new dependency — `ffprobe` ships with the already-required `ffmpeg`.
- Fully hermetic: `httpx.MockTransport` + a stubbed `_probe_duration_ms` (or a
  mocked `subprocess.run`) + pure argv/parse helpers cover request building, the
  bytes → descriptor mapping, and every probe-failure path with no network and
  no binary.
- Fail-loud everywhere a wrong duration could leak (missing/garbled probe,
  missing binary, non-resolvable sink URI), matching the repo's §11 ethos.

### Negative / Neutral

- The provider now depends on `ffprobe` at *synthesis* time (previously only the
  render step needed a binary). Documented and fail-loud; the integration smoke
  skips when `ffprobe` is absent.
- A read-only import from `app.media.composition.ffmpeg` couples the TTS adapter
  to the composition module's `resolve_local_path` / `CompositionError`. Accepted
  as reuse (precedent: the thumbnail renderer) over re-implementing path
  resolution.
- Constrains the injectable sink to file-resolvable URIs (no `mem://`), unlike
  `http_tts`. Documented in the module docstring and asserted by a test.

## Alternatives considered

- **Patch `HttpTtsProvider` to make the header optional / probe when absent.**
  Rejected: it would fork one adapter's behavior on a runtime branch and pull an
  ffprobe dependency into the generic adapter. A second, clearly-named adapter is
  cleaner and keeps the header contract intact for backends that honor it.
- **Pipe bytes to `ffprobe` via stdin (`pipe:0`).** Rejected: less reliable for
  some containers (ffprobe wants a seekable input for accurate duration) and it
  fights the descriptor-not-bytes / write-then-probe model.
- **A pure-Python container parser (e.g. `mutagen`).** Rejected: a new dependency
  for a job `ffprobe` already does, and it would only cover the formats the
  library supports.
- **Wire it into `config.py` / the composition root now.** Out of scope — this PR
  owns only `backend/app/media/tts/` + tests; composition wiring is a follow-up.

## Out of scope / follow-ups

- Composition-root / `config.py` wiring to select this adapter by setting (the
  owner's follow-up).
- Streaming / chunked synthesis and other `response_format`s beyond the
  pass-through default.

## References

- ADR 0019 (Media Production layer + deferred adapters), ADR 0022 (generic HTTP
  TTS adapter), ADR 0023 (ffmpeg pure/impure split + `resolve_local_path`),
  ADR 0007/0021 (httpx adapter hardening idiom).
- CLAUDE.md §3.3 (Media TTS), §4 (tools vs agents), §6 (provider neutrality),
  §9 (scope discipline).
