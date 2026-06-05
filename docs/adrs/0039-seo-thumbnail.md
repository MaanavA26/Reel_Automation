# ADR 0039: SEO Metadata Builder + Thumbnail Renderer — the Publishing-Support Surface

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

A faceless short-form upload lives or dies on its **title, description, tags, and
thumbnail** — they drive the impressions and the click-through that drive views
(and therefore revenue). This is the publishing-support surface CLAUDE.md §3.4
names, sitting just downstream of the Deep Research creator packet (§5.4): the
`CreatorPacket` carries the creative material (hooks, key facts) and its source
`Report` carries the provenance (citation urls), and both need to become the
fields an upload form expects.

Per CLAUDE.md §4 this is **tool** work, not agent work: turning already-synthesized
material into upload metadata, and shelling out to `ffmpeg` to render an image, are
both deterministic transformations — no judgment, no LLM. The judgment (what to
narrate, which hook is punchy) already happened upstream in the Short-Form Content
Strategist (§5.6).

Two sub-problems with different shapes:

1. **Metadata** is a pure *value* derivation — text in, text out, no I/O.
2. **Thumbnail** is a *rendered file*, shelling out to a binary the sandbox/CI may
   not have — the exact tension ADR 0023 solved for video composition.

## Decision

**Ship a new `app/seo/` package (`MetadataBuilder` → `VideoMetadata`) and a new
`app/media/thumbnail.py` (`ThumbnailRenderer` → `Thumbnail`), each mirroring the
ADR 0023 pure/impure split appropriate to its shape.**

### 1. `MetadataBuilder` is a pure value derivation

`MetadataBuilder.build(*, packet, report) -> VideoMetadata` is deterministic: same
inputs → identical output, no `_gen_id`, no `now()`, no I/O. `VideoMetadata` is
therefore a **value DTO** — no minted id, no timestamp (the `KeyFact`/`HookIdea`
"no id at v1" sub-unit precedent, not the `RenderedVideo` artifact pattern) — but
it still carries a `produced_via="seo:deterministic"` provenance string, symmetric
with the media DTOs. Both `packet` and `report` are required: the headline material
(hooks, key facts) lives on the packet, the source urls live only on
`Report.citations`. The builder asserts `packet.report_id == report.id` and raises
`MetadataError` on mismatch — an implied precondition made loud, never silently
mixing two jobs' artifacts.

### 2. Hard platform invariants, verified not guessed

- **Title ≤ 100 chars** is an *enforced invariant* (Pydantic `max_length` + a
  deterministic word-boundary truncation with a single-char `…`), tested exactly at
  the boundary — not a hope.
- **Hashtags capped at 5** (`MAX_HASHTAGS`), tags at 15 (`MAX_TAGS`). Verified
  2026-06: YouTube ignores **all** hashtags on a video once there are more than 15
  (a hard cutoff, not a gradual penalty), and only the first three render above the
  title; 3–5 is the discovery sweet spot. Staying well under the cap means
  attribution is never silently dropped.

### 3. §11 carried to the discovery surface

The repo-wide evidence-vs-inference discipline (CLAUDE.md §11) reaches the headline:
a hook whose referenced finding is **disputed**, or a disputed key fact, is **never
promoted to the title** (the "don't amplify unverified claims in polished outputs"
ethos). Disputed/weakly-supported facts still appear in the description **body**
with an inline transparency marker (`(note: sources disagree)` / `(note: single
source)`) — full transparency, just never as the headline. The disputed/weak
classification uses the same `disputed` / `weakest_support` ordering as the M11/M12
`finding_caveat_kind` predicate (a `KeyFact` carries those identical flags), inlined
rather than calling the `Finding`-typed predicate to avoid a cross-shape adapter, so
the SEO surface cannot drift from the report's caveats / packet's warnings.

### 4. Tags are a stdlib-only deterministic floor

`pyproject.toml` is off-limits, so no NLP dependency: a small documented stopword
filter over the title + section headings + key-fact statements, lowercased,
deduped, ≥3 chars. A deliberate floor, not a linguistics engine. LLM-polished copy
(a model rewriting title/description for punch) is a documented **future
enhancement** layered *over* this floor — it would never author tags/hashtags or
relax the title-length invariant, the same way `report.py` layers model prose over
code-derived citations.

### 5. `ThumbnailRenderer` mirrors ADR 0023 exactly

`build_thumbnail_args(*, video_path, title_textfile, fontfile, output_path,
timestamp_s, width, height) -> list[str]` is **pure** (no temp files, no id mint,
no I/O) — the argv is token-assertable with no binary. `ThumbnailRenderer.render`
resolves the video URI (**reusing** `composition.ffmpeg.resolve_local_path`, not
reimplementing it), derives the seek timestamp from the video duration, writes the
title to a temp sidecar, mints the `thumb_…` id, calls the pure builder, and runs
the argv via a single mockable `_run` `subprocess.run` seam off the event loop. The
graph: `-ss <t> -i <video> -frames:v 1` with one `-vf` that scale+pads (letterboxes,
never stretches) then `drawtext`. `Thumbnail` is a rendered-artifact DTO (id +
`produced_via="thumbnail:ffmpeg"`), defined in the module itself (not in the
out-of-scope `app/media/schemas.py`).

### 6. Title overlay via a `textfile=` sidecar (the escaping dodge)

Inline `drawtext` text needs a thorny escape of `\ : ' %` and newlines. Instead the
title is written to a temp sidecar and read via `drawtext=textfile='<path>'`, so we
escape only the *path* — reusing the exact filtergraph-path escape `ffmpeg.py`
established for the subtitles path. The pure argv stays cleanly assertable and the
escaping minefield is sidestepped.

### 7. Thumbnail dimensions are parameterized, not decided in code

Default `1280x720` (16:9) — verified the official YouTube thumbnail size, since
YouTube renders the thumbnail in 16:9 containers across search/home/channel even
for a 9:16 Short. But the vertical-vs-16:9 debate is real, so `width`/`height` are
parameters (like `build_ffmpeg_args`), sidestepping the contested choice: a caller
can request `1080x1920` without a code change.

### 8. `drawtext` needs a font → two integration skip conditions

`fontfile` is an explicit constructor parameter (the renderer never guesses a system
font). The `@pytest.mark.integration` real-render test therefore skips on **two**
conditions — `ffmpeg` absent *or* no locatable font — beyond the composition test's
single skip; the input video is synthesized via `lavfi` like the composition test.

## Consequences

### Positive

- The whole metadata derivation is equality-testable with no LLM, and the thumbnail
  argv is token-assertable with no binary — the M-LP offline posture.
- The §11 headline guard means a polished, click-optimized title can never rest on
  a contradicted finding; the body stays fully transparent.
- Title-length and hashtag-cap are enforced invariants verified against the real
  platform rules, not folklore.

### Negative

- Tag extraction is a stopword floor, not real keyword research; the LLM-polish
  enhancement is the planned uplift.
- Real thumbnail render quality (font rendering, layout) is validated only on a
  binary+font-equipped run — the offline ceiling ADR 0023 named, here doubled by the
  font requirement.

### Neutral

- No wiring/composition-root change: both are standalone tools (the media
  orchestrator chaining packet → metadata → render is a future seam). No new
  dependency (stdlib `re`/`subprocess`/`shlex` + existing Pydantic DTOs). `config.py`,
  `main.py`, `pyproject.toml`, `app/media/__init__.py`, and `app/media/composition/`
  are untouched.

## Alternatives considered

### Option A — Inline `drawtext=text='…'` instead of a sidecar

**Pros:** no temp file. **Cons:** a thorny escaper for `\ : ' %`/newlines that must
itself be tested with adversarial titles, and the random temp path would otherwise
already be in the argv. **Rejected:** the sidecar reuses the existing path-escape and
keeps the pure argv assertable.

### Option B — Give `VideoMetadata` an id + timestamp like `RenderedVideo`

**Pros:** symmetry with media artifacts. **Cons:** metadata is a *derived value*, not
a produced file with a lifecycle; an id/timestamp would make the pure builder
non-deterministic and un-equality-testable. **Rejected** for the value-DTO shape (the
`KeyFact`/`HookIdea` precedent).

### Option C — Pick a single thumbnail dimension in code

**Pros:** less config. **Cons:** the 16:9-vs-9:16 choice is genuinely contested.
**Rejected:** parameterize and document the default, sidestepping the uncertainty.

## References

- [CLAUDE.md](../../CLAUDE.md) §3.4 (publishing support), §4 (tools vs agents), §5.4
  (creator packet), §7/§9 (scope, quality), §11 (evidence vs inference).
- [ADR 0023](0023-ffmpeg-composition.md) (the pure-construction / mockable-execution
  split this mirrors; `resolve_local_path` + path-escape reuse).
- [ADR 0018](0018-creator-packet.md) (the `CreatorPacket` + `KeyFact` grounding flags
  this consumes) / [ADR 0017](0017-report-generation.md) (the `Report` + citations).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Publishing-support surface.
