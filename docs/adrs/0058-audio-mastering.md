# ADR 0058: Audio mastering — two-pass loudnorm to -14 LUFS + 44.1 kHz

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

The first real `topic → video` render (the last-mile validation, CLAUDE.md §13)
produced narration measured at **-22 LUFS mono 24 kHz** — roughly 8 LU under the
platform-competitive loudness target and 24 kHz where the publishing baseline is
44.1 kHz. A short-form clip that plays this quiet is an immediate Definition-of-Done
miss against the surrounding feed: it sounds amateurish next to native content. This
is P1 Step 1 of the creative-quality overhaul (epic #125 / issue #128): master the
audio so it plays at competitive loudness and a standard sample rate.

The composition layer is a deterministic **tool**, never an agent (CLAUDE.md §4):
there is no judgment here, only the fixed loudness target and ffmpeg's measurement
math. The existing renderer already enforces the load-bearing
**construction/execution split** (ADR 0023): `build_ffmpeg_args` is a pure argv
builder unit-testable with no binary present, and `render` is the subprocess seam.
Audio mastering must preserve that split.

Two further forces shaped the placement. First, accurate loudness normalization is
a **two-pass** operation: ffmpeg's `loudnorm` filter must first *measure* the source
and then re-apply itself with those measurements (a one-pass `loudnorm` does a
dynamic, audibly-pumping correction; supplying `measured_*` + `linear=true` makes
pass two a single fixed gain to target). A measure pass is, by definition, I/O — so
it cannot live in the pure builder. Second, the QC gate (issue #126, council
coupling C1/C2) will measure a *rendered* file against the *same* loudness target,
so the target constants, the measure-pass argv, the stats model, and the parser must
be a **single shared source**, not duplicated between the renderer and the gate.

Music ducking (a background bed mixed under the VO with sidechain compression) is a
natural neighbour but is **out of scope**: it requires a royalty-cleared bed that
does not yet exist, and adding a no-op `music_bed_path` seam now would be speculative
overbuild (CLAUDE.md §7). It is deferred to a follow-up.

## Decision

### 1. A shared loudness primitives module — the single source (C1/C2)

`backend/app/media/composition/loudness.py` holds everything loudness-pure:

- the Definition-of-Done **target constants** `TARGET_I` (-14 LUFS), `TARGET_TP`
  (-1 dBTP), `TARGET_LRA` (11 LU), and `OUTPUT_SAMPLE_RATE` (44100) — referenceable
  by both the render path and the QC gate so the DoD numbers live in exactly one
  place (council coupling C2);
- `LoudnessStats`, a strict (`extra="forbid"`) Pydantic model of the five fields
  ffmpeg's first pass prints that pass two needs back (`input_i`, `input_tp`,
  `input_lra`, `input_thresh`, `target_offset`), matching the media-layer DTO
  convention (`media.schemas`);
- `build_loudnorm_measure_args(audio_path)`, the **pure** argv for the analysis
  pass (`loudnorm=…:print_format=json`, decoding to `-f null -`);
- `parse_loudnorm_stats(stderr_text)`, the **pure** parser.

The QC gate imports these directly — one import surface, no drift (C1).

### 2. The parser lifts five named fields and tolerates ffmpeg's stderr shape

`loudnorm`'s JSON carries ~10 keys (`output_i`, `normalization_type`, …) but only
five feed pass two, and the model is `extra="forbid"`; so the parser lifts those
five **explicitly** rather than splatting the dict (which would raise on the extra
keys). The JSON values are *strings* (`"input_i": "-22.50"`), so `LoudnessStats`
fields are plain floats (Pydantic coerces) — deliberately **not** strict-typed, or
the realistic fixture would fail to parse. The JSON is the *last* brace block on
stderr after ffmpeg's `[Parsed_loudnorm_0 @ …]` preamble, so the parser scans for
the final `{` and uses `JSONDecoder.raw_decode` (which stops at the object's close,
ignoring trailing log lines) rather than `json.loads(whole_stderr)`. A missing or
malformed analysis raises loud — pass two depends on every field — mirroring the
`parse_ffprobe_duration_ms` fail-loud discipline (ADR 0047).

### 3. The pure builder emits pass two; it stays pure

`build_ffmpeg_args` gains a required `loudness: LoudnessStats` parameter and routes
the narration through the **complex** filtergraph (not `-af`, which would not expose
a mappable label): `[{audio_index}:a]loudnorm=I=…:TP=…:LRA=…:measured_*=…:offset=…:linear=true,aresample=44100[aout]`,
the audio map becomes `-map "[aout]"`, and the output sample rate is pinned with
`-ar 44100`. The measured floats are formatted to fixed precision so the argv stays
deterministic and assertable. Both the burn-in and soft-mux caption branches share
this single audio map, so neither is touched. The builder still creates no temp
files, mints no ids, and runs no subprocess — the argv remains fully hermetic.

### 4. The measure pass runs in the execution seam, off the event loop

`render` gains `_measure_loudness(audio_path)`: it builds the pure measure argv, runs
it through the **same** `_run` subprocess seam (so a missing binary / non-zero exit
normalize to `CompositionError` exactly like a render), and parses the stderr. It is
invoked via `asyncio.to_thread` before `build_ffmpeg_args` (mirroring the
`nvidia.py` / `subtitles_filter_available` off-event-loop pattern, ADR 0047), and its
result is handed to the builder. The two-pass cost is one extra ffmpeg invocation per
render — acceptable for the quality gain and bounded by the existing render timeout.

### 5. Music ducking is out of scope

No `music_bed_path`, no mix node, no no-op seam — adding one now would be speculative
(CLAUDE.md §7). The clean seam for it is the same `[aout]` audio chain: a future ADR
adds a second audio input and a `sidechaincompress`/`amix` stage before `[aout]`.

## Consequences

**Positive.** Narration is mastered to the platform-competitive -14 LUFS / -1 dBTP
target at 44.1 kHz — the #128 DoD. The loudness primitives are a single shared source
the QC gate (#126) reuses verbatim, so the target and the measurement logic cannot
drift between producing and checking audio (C1/C2). The construction/execution split
(ADR 0023) is preserved: the builder stays pure and hermetically argv-testable, the
measure pass is the execution-side seam. Stdlib + Pydantic + the already-required
ffmpeg binary — no new dependency.

**Negative / deferred.** Each render now runs ffmpeg **twice** (analyse, then render)
— one extra subprocess per video. Music ducking is deferred. Loudness is measured on
the *source* narration; a future QC gate measuring the *rendered* file is the
end-to-end verification (this ADR ships the producing half of that loop).

**Risks.** Low and bounded. The two-pass path is exercised by a real-ffmpeg
`@pytest.mark.integration` test that renders a 440 Hz tone (not silence — digital
silence yields a degenerate `-inf`/`-70` analysis that breaks pass two) and asserts
the muxed track is 44.1 kHz; the pure builder, the parser, and the `_measure_loudness`
seam are fully hermetic. No schema, router, or pipeline-behavior change beyond the new
required builder parameter (whose only caller is `render`, updated in lockstep).

## Alternatives considered

- **One-pass `loudnorm` (no measure pass).** Rejected: one-pass mode is a dynamic
  correction that audibly pumps and does not hit the integrated target accurately.
  Two-pass with `measured_* + linear=true` is the documented accurate mode and the
  only one worth shipping for a quality gate.

- **`-af` instead of the complex filtergraph for the audio chain.** Rejected:
  `-map "[aout]"` requires `[aout]` to be defined in `-filter_complex`; an `-af`
  chain produces no mappable label and would not compose with the existing complex
  video graph cleanly.

- **`loudness=loudnessnorm` / EBU R128 normalization outside ffmpeg.** Rejected:
  ffmpeg's `loudnorm` *is* EBU R128, the binary is already a hard dependency
  (ADR 0019/0023), and a second audio tool would add a dependency for no gain.

- **Put the parser/model/constants in `ffmpeg.py`.** Rejected: the QC gate (#126)
  needs them without importing the renderer; a dedicated `loudness.py` is the
  honest single source (C1) and keeps `ffmpeg.py` focused on the render argv.

- **A no-op `music_bed_path` seam now.** Rejected as speculative overbuild
  (CLAUDE.md §7): no bed exists, so the seam would have no consumer and no test
  beyond "it does nothing". Deferred to its own ADR when a cleared bed lands.
