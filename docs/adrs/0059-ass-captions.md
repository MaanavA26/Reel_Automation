# ADR 0059: Styled burned-in captions via `format_ass`

- **Status:** Accepted
- **Date:** 2026-07-01
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

P1 Step 2 of the creative-quality overhaul (epic #125 / issue #131). The renderer
already burns captions in via ffmpeg's `subtitles` filter (ADR 0058 / issue #116),
but it feeds that filter a plain **SRT** — unstyled white default-font text. Against
a native short-form feed that reads as amateurish: no brand font, no heavy outline
for legibility over busy B-roll, no entrance/exit fade. The creative-quality
Definition-of-Done for the epic (#125, driving issue #131 — *unverified* repo-lore,
not yet a checked-in rubric doc) calls for legible, on-brand, **burned-in** captions.

The composition/subtitle layer is a deterministic **tool**, never an agent
(CLAUDE.md §4): there is no judgment here, only a fixed mapping from a structured
`CaptionTrack` + a `CaptionStyle` to ASS text. The existing layer already ships pure
`format_srt`/`format_vtt` formatters (subtitle generation is listed tool work) and
the renderer enforces the construction/execution split (ADR 0023). This step extends
those patterns rather than inventing new ones: one more pure formatter, one more
typed style DTO, and a minimal render-path branch.

Why ASS and not styled SRT/VTT: SRT carries no styling and VTT styling (CSS cues) is
not honoured by ffmpeg's burn-in filter. **Advanced SubStation Alpha (`.ass`)** is
the format libass (the engine behind the `subtitles` filter) renders with full font,
colour, outline, alignment, margin, and fade control. It is the standard choice for
burned-in styled captions.

## Decision

### 1. A `CaptionStyle` DTO carries brand styling (`media.schemas`)

A strict (`extra="forbid"`) Pydantic value object next to the other media DTOs:
`font_name`, `font_size`, `primary_colour`, `outline_colour`, `outline_width`,
`fade_in_ms`, `fade_out_ms`, and `margin_fraction` (default `0.10` = 10% L/R safe
inset). Colours are stored as designer-facing `#RRGGBB` hex and converted to ASS's
wire format only inside the formatter — never stored pre-converted.

**Input validation (#132 review).** `font_name` is rejected (Pydantic
`field_validator`) if it contains a comma — the ASS `Style:` field delimiter, which
would shift every downstream field — or a newline/control char that would break the
single-line row. `primary_colour`/`outline_colour` are validated against a shared
`_RGB_HEX = ^#[0-9A-Fa-f]{6}$` (exactly one leading `#`, exactly six hex digits), the
same anchored pattern `subtitles.base._ass_colour` uses, so `123456`, `##123456`,
`#12345`, and `#GGGGGG` are all rejected before they reach the formatter.

**Frozen (#132 review).** Unlike the other four media DTOs, `CaptionStyle` is
`ConfigDict(extra="forbid", frozen=True)`. A module-level
`DEFAULT_CAPTION_STYLE = CaptionStyle()` is the single shared default referenced by
all three `render` signatures (which also sidesteps ruff B008,
function-call-in-default-argument). Because that one instance is shared across every
render that passes no explicit style, freezing it prevents a caller from mutating the
shared default in place and leaking the change to unrelated renders. The other DTOs
stay non-frozen — none has a shared mutable default worth protecting, so freezing them
would widen the blast radius for no benefit.

### 2. A pure `format_ass(track, *, style, width, height)` formatter (`subtitles.base`)

Parallel to `format_srt`, reusing `_validate_cues`:

- `[Script Info]` pins `ScriptType: v4.00+`, `PlayResX`/`PlayResY` = the real output
  frame (so font size and margins scale to the actual resolution, not libass's
  phantom 384x288 default), `WrapStyle: 0`, and `ScaledBorderAndShadow: yes`.
- `[V4+ Styles]` is one brand `Style:` row built from `CaptionStyle`, with the exact
  canonical **23-field** V4+ layout, `Alignment=2` (bottom-centre), and
  `MarginL`/`MarginR` = `round(margin_fraction * width)`. The `Format:` field list
  and field order are a module constant (`_ASS_STYLE_FORMAT`) shared with the tests.
- `[Events]` is one `Dialogue:` per cue; each cue's text is prefixed with a
  **cue-level** `{\fad(in,out)}` override, then the escaped cue text. Per-cue margins
  are `0,0,0` so they inherit the Style row's real margins.

Three correctness locks (all unit-tested, because **no libass runs in CI** so a
plausible-but-wrong ASS file would otherwise pass silently):

- **Centisecond timestamps, truncate not round.** ASS uses `H:MM:SS.cc` (1-digit
  hour, 2-digit centiseconds) — the existing `_format_timestamp` (2-digit hour,
  3-digit ms) cannot be reused, so a new `_format_ass_timestamp` helper. **ms→cc is
  truncated** (`(total_ms % 1000) // 10`), a locked decision: truncation always
  yields 0–99 and can never overflow into a second-carry, whereas rounding could push
  e.g. 995 ms to `round(99.5) = 100` (an invalid centisecond needing carry logic).
  The sub-10 ms loss is invisible at caption granularity. Boundary tested
  (1995 ms → `0:00:01.99`, 995 ms → `0:00:00.99`).
- **Colour conversion `#RRGGBB` → `&HAABBGGRR`.** ASS colours are inverted-alpha,
  BGR-ordered: alpha `00` = **opaque**, byte order A,B,G,R. Captions are opaque so
  alpha is fixed `00`. Isolated as a pure `_ass_colour` helper, unit-tested with an
  **asymmetric** triple (`#123456` → `&H00563412`) so a partial B/G/R confusion is
  caught.
- **Override-char escaping by replacement, not backslash.** In ASS, `{` opens an
  override block, `}` closes it, `\` introduces specials (`\N`, `\h`). There is **no
  reliable literal-brace escape** — outside `{}` a backslash passes through but the
  brace still opens a block, so `\{` does not neutralize it; a bare `\N` even becomes
  a line break outside braces. So cue text **replaces** `{`/`}`/`\` with safe
  lookalikes (fullwidth braces, a forward slash) and collapses newlines to spaces.
  The literal `{\fad(...)}` is prepended *after* escaping the cue text, never escaped
  itself. Tested: a cue containing `{`, `}`, `\`, `\N` yields a text portion with no
  raw override characters.

### 3. `render()` writes `.ass` on burn-in, `.srt` on soft-mux (`composition.ffmpeg`)

The probe (`subtitles_filter_available`) now runs **before** the subtitle file is
written. On the burn-in path (libass present) the render writes a transient `.ass`
via `format_ass(captions, style=caption_style, width=width, height=height)`; on the
soft-mux fallback (no libass) it keeps writing a plain `.srt` via `format_srt`,
because the `mov_text` muxed-subtitle codec **cannot carry ASS** styling. The actual
path (either extension) is tracked in one variable and removed in `finally`. `render`
gains a `caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE` parameter.

The burn-in branch of `build_ffmpeg_args` is **unchanged**: it already uses the
`subtitles='...'` filter, which auto-detects `.ass` from the file content. We
deliberately keep `subtitles=` rather than switching to the dedicated `ass=` filter,
so `subtitles_filter_available` (which probes for the `subtitles` filter) stays the
correct capability gate and the existing filtergraph escaping (CodeRabbit #117) is
untouched. The pure builder simply receives whatever path `render` wrote.

### 4. Protocol + fake stay in sync

The `CompositionService` Protocol and `FakeCompositionService.render` gain the same
default-valued `caption_style` parameter, so all three signatures match and existing
callers (which pass no style) keep working unchanged.

## Honesty notes (explicit, per the brief)

- **Cue-level fade only — NOT word-level karaoke.** This ships a single
  `{\fad(in,out)}` per cue. It is **not** the word-level karaoke ("animated
  captions") the project's creative references mean, and it does **not** satisfy the
  "animated captions" Definition-of-Done line. Word-karaoke is deferred to a separate
  future step (§D2 below).
- **≤2 lines is not guaranteed here.** `WrapStyle` + margins bias toward a tight
  layout, but the actual line count depends on font metrics (which need libass to
  measure) and on upstream cue segmentation. This step does not enforce a line cap.
- **Never visually validated hermetically.** There is no libass in CI, so the tests
  assert the ASS *text* shape (sections, field counts, timestamps, colours, margins,
  fade, escaping) and the *argv* (`.ass` written + fed via `subtitles=`). A real
  on-screen render check is a last-mile follow-up (CLAUDE.md §13).

## Consequences

**Positive.** Burned-in captions now carry the brand font, a heavy legibility
outline, bottom-third placement with a 10% safe margin, and an entrance/exit fade —
a concrete DoD step up from default-font white SRT. The change extends the existing
pure-formatter + construction/execution patterns with no new dependency (stdlib +
Pydantic + the already-required ffmpeg/libass). The soft-mux fallback still produces
a valid MP4 on libass-less builds.

**Negative / deferred.** Word-level karaoke ("animated captions") is deferred (§D2);
the ≤2-line cap is not enforced; the ASS output is unverified visually until a
last-mile real render. The soft-mux path loses styling entirely (mov_text limitation)
— acceptable as a degraded fallback.

**Risks.** Low and bounded. The field-count assertion (Style/Dialogue rows vs their
`Format:` lines) is the closest hermetic proxy for "libass will accept this"; the
truncate and colour locks are boundary-tested. Render-path tests mock the probe both
ways so the suite is deterministic regardless of the local ffmpeg's libass support.
No schema-breaking change: the new `caption_style` parameter is defaulted, so every
existing caller is unaffected.

## Alternatives considered

- **Styled SRT / VTT instead of ASS.** Rejected: SRT has no styling and ffmpeg's
  burn-in filter does not honour VTT's CSS cue styling. ASS is the format libass
  renders with full control.
- **The `ass=` filter instead of `subtitles=`.** Rejected: `subtitles=` already
  auto-detects `.ass`, and the capability probe targets the `subtitles` filter;
  switching to `ass=` would invalidate the probe and force re-testing the
  filtergraph escaping for no functional gain.
- **Word-level karaoke (`\k`/`\kf` timing) now.** Rejected as out of scope: it needs
  per-word timings the pipeline does not yet produce and is a distinct feature. It is
  the natural next step (§D2) on the same `format_ass` seam — a future ADR adds
  per-word `\kf` spans once word timings exist.
- **Leaving `CaptionStyle` non-frozen.** Originally chosen to match the surrounding
  DTOs (no other media DTO is frozen), but **reversed in the #132 review**: because
  `DEFAULT_CAPTION_STYLE` is a shared module-level singleton passed by default into
  every `render`, an in-place mutation would leak across unrelated renders. The narrow
  fix — `frozen=True` on `CaptionStyle` only, not the other DTOs — protects the shared
  default at the cost of one deliberate, documented divergence from the local house
  style (see Decision §1).

### D2 — deferred follow-up

Word-level karaoke "animated captions": per-word highlight timing via ASS `\kf`
spans, driven by per-word timestamps from a forced-alignment / word-timing upstream.
A separate ADR when that timing source lands.
