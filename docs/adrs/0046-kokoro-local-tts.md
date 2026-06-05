# ADR 0046: Local Kokoro TTS Provider (primary, zero-cost, offline)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 introduced the Media Production layer as provider-neutral, fully-faked
seams of deterministic **tools** (CLAUDE.md §4) and deferred the concrete
`TTSProvider`s behind `tts/base.py`. ADR 0022 landed the first one,
`HttpTtsProvider` — a *network* adapter over a generic REST endpoint. That path
costs money per call and requires a vendor.

This ADR lands the **primary** TTS backend: a *local, zero-cost* provider that
runs the Apache-2.0 [Kokoro-82M](https://hf.co/hexgrad/Kokoro-82M) model entirely
on the operator's machine via ONNX Runtime (CPU). No network, no vendor, no
per-call cost — the right default for a faceless-shorts content engine that
narrates many clips. It is the local-first counterpart to `HttpTtsProvider`
behind the *same* protocol seam, swappable by configuration.

The discriminating constraints are the **fixed protocol seam**
(`async synthesize(*, text, voice) -> SynthesizedSpeech`) and the **offline build
sandbox** (no PyPI, so `kokoro-onnx`/`onnxruntime` cannot be installed here). The
adapter must therefore import clean without the package and be fully testable
without it.

## Decision

**Ship `KokoroTtsProvider` (`media/tts/kokoro.py`, `name = "kokoro"`)** over the
`kokoro-onnx` package's `Kokoro(model_path, voices_path).create(text, voice=…)
-> (samples, sample_rate)` API. Key choices:

- **Pure/impure split (the load-bearing design, mirrors ffmpeg ADR 0023).** The
  single impure seam is `_create_waveform` — it lazy-imports `kokoro_onnx`,
  builds+caches the model, and calls `.create()` to return a
  `(samples, sample_rate)` waveform. **Everything after it is pure code outside
  the seam:** `encode_wav_pcm16` (stdlib `wave`, **numpy-free**) and the exact
  `duration_ms_from_samples`. Mocking the seam at the *waveform* level (not the
  bytes level) is what makes "compute duration from the produced audio"
  hermetically *demonstrable* — the test feeds 48000 samples @ 24kHz and asserts
  `duration_ms == 2000` and that real `RIFF` bytes reach the sink.
- **Duration is exact from the sample count** (`round(samples * 1000 / rate)`).
  Because Kokoro returns the waveform itself, there is no need for the
  format-specific `X-Audio-Duration-Ms` header the HTTP adapter requires (ADR
  0022), nor for an `ffprobe` fallback — that branch would be **dead code**, so
  per §7 (no-overbuild) it is documented as unnecessary rather than implemented.
- **Lazy import, fail loud.** The `kokoro_onnx` import lives *inside* the
  inference method (mirroring `pypdf` in `services.ingestion.pdf_parser`, ADR
  0014), so this module imports clean offline. A missing package raises
  `KokoroTtsError` with the exact `pip install kokoro-onnx` + model-files hint;
  a missing/invalid model or voices file fails loud with a path hint.
- **Async off the event loop.** `synthesize` runs the blocking CPU inference via
  `asyncio.to_thread` (mirroring ffmpeg's `_run`), so a synth never blocks the
  loop.
- **Per-call `voice` wins.** The protocol passes `voice` to `synthesize`; it is
  forwarded to `.create(voice=voice)` and recorded on the DTO. The constructor's
  `voice` is only the default.
- **Model cached.** The `Kokoro` instance is built lazily on first synth and
  cached (the ONNX graph is expensive to build; reloading per call would be
  wasteful).
- **Storage-neutral via an injected `AudioSink`** (reused from `http_tts`, not
  redefined): the adapter persists WAV bytes through the sink and records the
  returned `file://` URI — the scheme the ffmpeg composition adapter resolves
  (`composition.ffmpeg.resolve_local_path`). The media layer traffics in
  descriptors, never bytes (ADR 0019).
- **WAV PCM16 encoding.** Kokoro emits float32 samples in ~`[-1, 1]`; the encoder
  clamps to that range and scales to int16 (`*32767`, not `*32768`, to avoid a
  `+1.0` overflow). This assumption is documented at the function.

### Scope discipline

Construction takes the model config (`model_path`, `voices_path`, `voice`,
`lang`, `speed`) and the `sink` — **never** global `Settings`, so `config.py` is
untouched. The change is **exactly one new adapter module + its test** (plus the
ADR / CHANGELOG / ROADMAP lines). The lazily-imported `kokoro_onnx` is silenced
for mypy with an **inline `# type: ignore[import-not-found]`** at the import site
(used in the offline sandbox where the package is absent, a no-op once installed),
*not* a `pyproject.toml` override — `pyproject.toml` is config the operator owns
(and a follow-up wiring task), so it stays untouched (§9 scope discipline). The
constructor `voice` is the config-surface default a future wiring root can record;
the protocol's required per-call `voice` always wins, so it is exposed but not
consulted on the synth path. `tts/base.py`, `tts/__init__.py`,
`media/composition.py`, and the runtime dependency list are untouched —
`kokoro-onnx` is **not** added to `pyproject.toml` dependencies (it pulls heavy
ONNX/torch-free runtime; it is an opt-in install for the operator who wants the
local backend, surfaced via the install hint). Factory/composition-root wiring
and a `Settings` field are an explicit follow-up.

## Consequences

### Positive

- A *zero-cost, fully-offline* `TTSProvider` exists behind the ADR 0019 seam —
  the right default narration backend for a high-volume shorts engine.
- Provenance holds: `produced_via = "tts:kokoro"`, symmetric with `"tts:http"` /
  `"tts:fake"`.
- Fully offline-verifiable without `kokoro-onnx`/`onnxruntime`/`numpy`: the
  waveform-level seam mock exercises the WAV encoding, the exact duration math,
  the per-call-voice override, the sink hand-off, model caching, and the
  fail-loud install hint.
- Duration is *exact* (waveform-derived), not a contract assumption like the
  HTTP adapter's header.

### Negative

- **Live behavior is unverifiable in the sandbox** (no PyPI). The real model path
  is covered by a `@pytest.mark.integration` smoke test that skips unless the
  package *and* the model + voices files are present (paths from
  `REEL_KOKORO_MODEL_PATH` / `REEL_KOKORO_VOICES_PATH`).
- **The `kokoro-onnx` API + the float32 `[-1, 1]` sample range are assumptions**
  pinned by the hermetic fixtures; a real run confirms them. The adapter fails
  loud rather than guessing.

### Neutral

- No schema change (`SynthesizedSpeech` already exists). No runtime-dependency
  change (the package is an opt-in operator install). No wiring change. No
  `pyproject.toml`/`config.py` change — the mypy silence is an inline ignore.

## Deferred (with reasons)

- **Composition-root / factory wiring + a `Settings` field** — follows a media
  composition root; out of this adapter's scope (and would touch `config.py`).
- **Adding `kokoro-onnx` to `pyproject.toml` dependencies** — it carries a heavy
  ONNX runtime; kept an opt-in install until the local backend is selected by
  config, so the default install stays lean.
- **Streaming / phoneme-input / multi-language voice packs** — the protocol
  returns a single descriptor; these earn their own seam when a consumer needs
  them (§7/§13).
- **A non-WAV (e.g. mp3) encode** — WAV is lossless and ffmpeg-friendly; a
  compressed encode is a later option if storage matters.

## Alternatives considered

### Option A — Mock the inference seam at the *bytes* level

**Pros:** simpler test double. **Cons:** the duration math (the task's core
requirement) would be untested, since duration is derived from the waveform, not
the bytes. **Why rejected:** the waveform-level seam is what makes the
samples→duration computation demonstrable hermetically.

### Option B — Encode WAV with `soundfile`/numpy

**Pros:** one call. **Cons:** adds a dependency and makes the encoder untestable
offline (numpy absent in the sandbox). **Why rejected:** a stdlib `wave` encoder
over a plain iterable keeps the pure path numpy-free and hermetic.

### Option C — Recover duration via `ffprobe`

**Pros:** uniform with formats that don't expose a waveform. **Cons:** Kokoro
*does* return the waveform, so this is an unused exec seam — dead code. **Why
rejected:** §7 no-overbuild; the exact samples/rate math is simpler and correct.

## References

- Related: [ADR 0019](0019-media-production-layer.md) (the media seam this fills),
  [ADR 0022](0022-tts-adapter.md) (the sibling network `TTSProvider`; the
  injected `AudioSink` reused here), [ADR 0023](0023-ffmpeg-composition.md) (the
  pure/impure construction-vs-execution split + `file://` resolution mirrored
  here), [ADR 0014](0014-pdf-ingestion.md) (the lazy-import-offline-clean +
  mypy-override precedent).
- [CLAUDE.md](../../CLAUDE.md) §3.3 (media TTS), §4 (TTS is a deterministic tool),
  §6 (provider abstraction; local/open-source model role), §7/§13 (no speculative
  overbuild), §9 (scope discipline), §11 (provenance).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer (concrete adapters).
