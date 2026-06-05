# ADR 0023: FFmpeg Composition Adapter — Pure Construction / Mockable Execution

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0019 scaffolded the Media Production Layer as provider-neutral, fully-faked
seams and **deferred the concrete adapters** — including the real `ffmpeg`-backed
`CompositionService` — to a network/binary-gated milestone, the same deferral
M2/M5 made for the LLM and search fabrics (ADR 0003/0006). The build sandbox has
no bundled `ffmpeg` binary, exactly that constraint.

This ADR fills the composition slot: a concrete `FfmpegCompositionService` behind
the `CompositionService` protocol (`app/media/composition/base.py`). Per CLAUDE.md
§4 composition / FFmpeg assembly is **tool** work — deterministic execution, never
an agent (judgment about *what* to narrate lives upstream in the Deep Research
Short-Form Content Strategist, §5.6).

The central tension: a real renderer shells out to a binary the sandbox (and CI)
does not have, yet the adapter's logic must be testable. Shelling out is I/O the
test must not actually perform; the *command we construct* is logic the test
must verify.

## Decision

**Ship `app/media/composition/ffmpeg.py` with a load-bearing split between pure
command construction and impure execution, so the argv is unit-testable with no
ffmpeg binary present.**

### 1. Command construction is a pure function

`build_ffmpeg_args(*, audio_path, visual_paths, subtitles_path, output_path,
duration_ms, width, height) -> list[str]` takes **already-resolved local paths**
and returns the argv list. It creates no temp files, mints no random ids, and
performs no I/O — so the exact argv is assertable token-by-token against known
inputs. Everything non-deterministic (temp-file creation, the `.srt` write,
URI→path resolution, the output filename) lives in the async `render` method, on
the other side of the boundary. Burying a `NamedTemporaryFile` or an id mint
inside the builder would put a random token in the argv and defeat the design,
so it is forbidden by construction.

### 2. Execution is one thin, mockable subprocess seam

`render` resolves URIs, writes the caption `.srt`, calls the pure builder, then
runs the argv via a single private `_run(args)` method using stdlib
`subprocess.run(argv, capture_output=True, check=False, …)` wrapped in
`asyncio.to_thread` (off the event loop — `render` stays `async`, matching the
protocol; no new dependency, per scope). The hermetic test patches this one seam
(returning a `CompletedProcess`, or raising `FileNotFoundError`); the integration
test exercises it for real.

### 3. URI resolution is explicit, not magic

`resolve_local_path(uri)` (also pure) accepts a `file://` URI or a bare local
path and raises `CompositionError` on any other scheme (the fake's `fake://`,
`http://`, …) rather than handing a non-path to ffmpeg. The renderer never
guesses at a remote fetch — the boundary is loud.

### 4. Failures normalize to one `CompositionError`

Both the missing-binary case (`FileNotFoundError` → "ffmpeg binary not found")
and any non-zero exit surface as `CompositionError`, carrying the human-readable
command via `shlex.join(argv)` and a bounded **tail** of stderr (not megabytes).
A timeout is wrapped too. `shlex.join` is for the *message only* — execution
always uses the argv list, never a shell string, so there is no injection
surface. This mirrors the search fabric's `SearchError` / ingestion's
`FetchError`. The name `CompositionError` is local to this module; it does not
collide with the unrelated DI-root `services.composition.CompositionError`.

### 5. Duration mirrors narration; SRT is reused, not reimplemented

`RenderedVideo.duration_ms` is set from `audio.duration_ms` (the "video is as
long as its narration" rule the fake documents) — we do **not** `ffprobe` the
output, which would be a second binary and break determinism. The caption track
is rendered to a sidecar `.srt` via the in-layer `subtitles.base.format_srt`
(no duplicate timestamp formatting) and burned in with ffmpeg's `subtitles`
filter. The artifact id is minted once (the same-layer `_gen_id("vid")`) so the
output filename and the returned DTO id match. `produced_via="composition:ffmpeg"`.

## Consequences

### Positive

- The adapter's real logic — argv construction, URI resolution, error
  normalization — is fully covered offline, no binary required (the M-LP testing
  posture: MockTransport for httpx adapters, here a mocked subprocess).
- Drop-in behind the existing protocol; the `FakeCompositionService` contract
  (duration mirrors narration, dimensions echo the request, `RecordedRender`
  call-capture) is preserved, so consumers swap fake↔real freely.
- `shlex.join` in errors gives an operator a copy-pasteable command to reproduce
  a failed render.

### Negative

- The integration test verifies a *real* render only where `ffmpeg` is on PATH;
  in the sandbox/CI it skips. Render *quality* (codecs, filtergraph correctness)
  is therefore validated only on a binary-equipped run — the same offline ceiling
  ADR 0019 named.
- The filtergraph is deliberately minimal (scale+pad each visual, concat in
  order, audio as master clock). Transitions, per-visual timing, Ken Burns, etc.
  are not built — out of scope (§7 no-overbuild); the value is the split + error
  handling.

### Neutral

- No wiring/composition-root change: this is the adapter only (the media
  orchestrator that would chain TTS → subtitles → composition is still deferred,
  ADR 0019). No new dependency (stdlib `subprocess`/`shlex` + the existing
  Pydantic DTOs).

## Deferred (with reasons)

- **Richer filtergraphs** (transitions, per-visual durations from a timeline,
  background music ducking) — added when a real composition consumer specifies
  them, to avoid speculative surface.
- **Video (not just still-image) visuals** — the builder loops/concats stills;
  motion B-roll input handling is a bounded follow-up once the asset-bundle
  contract exists.
- **The Deep Research creator-packet → media handoff** — still ADR 0019's
  deferred cross-layer seam; this adapter consumes the media DTOs directly.
- **`ffprobe`-based output verification** — rejected for determinism; a future
  QA pass could add it behind an integration-only check.

## Alternatives considered

### Option A — Build the argv inline inside `render`

**Pros:** less code. **Cons:** the command can then only be tested by running
ffmpeg (or by asserting on a mock's call args through all the I/O), coupling the
logic test to the binary. **Rejected:** the pure builder is the whole point.

### Option B — `asyncio.create_subprocess_exec` instead of `subprocess.run` + `to_thread`

**Pros:** natively async, no thread. **Cons:** the task scoped to stdlib
`subprocess`/`shlex`; the async-subprocess mock is fiddlier (transport/protocol
plumbing) than patching one `subprocess.run`. **Rejected** for a simpler,
single mockable seam. (Revisitable if render concurrency ever matters.)

### Option C — Probe the rendered file for its true duration

**Pros:** the DTO reflects the actual output. **Cons:** a second binary
(`ffprobe`), non-deterministic, and contradicts the documented "duration mirrors
narration" contract. **Rejected.**

## References

- [CLAUDE.md](../../CLAUDE.md) §3.3 (Media layer — FFmpeg assembly), §4
  (composition/FFmpeg = tool, not agent), §7 (no overbuild), §9/§10 (scope, error
  handling, traceability).
- [ADR 0019](0019-media-production-layer.md) (the seam + fake this fills; adapter
  deferral).
- Related: [ADR 0013](0013-live-search-adapter.md) / [ADR 0007](0007-openai-compatible-llm-adapter.md)
  (first concrete adapter behind a faked fabric; offline-testable construction +
  integration smoke; error-boundary discipline).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer section.
