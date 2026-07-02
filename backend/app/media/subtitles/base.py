"""Subtitle generation: a deterministic seam plus pure SRT/VTT formatters.

Unlike the TTS and composition seams (which wrap a *future* vendor/binary and
therefore ship only a protocol + fake), subtitle generation needs no external
service for its core path — turning text segments + timings into caption files
is pure, deterministic computation. So this module ships **real** code now: the
`SubtitleService` protocol, a concrete `DeterministicSubtitleService`, and pure
stdlib SRT/VTT *formatters* that are fully unit-testable (CLAUDE.md §4 lists
"subtitle generation" as tool work).

The protocol is **synchronous**: the deterministic path is CPU-only, not I/O.
Forced alignment against the synthesized audio (the word-timing source for
karaoke captions) is a separate async seam — `app.media.alignment` (ADR 0062);
this module stays pure and only *renders* whatever word timings the cues
already carry.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.media.schemas import _RGB_HEX, Caption, CaptionStyle, CaptionTrack

# Canonical V4+ style field order (libass expects this exact 23-field layout in
# both the [V4+ Styles] `Format:` line and each `Style:` row). Kept as a constant
# so the formatter and its tests share one source of truth (ADR 0059).
_ASS_STYLE_FORMAT = (
    "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
    "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
    "Encoding"
)
# Canonical [Events] field order; each `Dialogue:` row matches this layout.
_ASS_EVENT_FORMAT = "Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text"
_ASS_STYLE_NAME = "Brand"


def _format_timestamp(total_ms: int, *, sep: str) -> str:
    """Render integer milliseconds as ``HH:MM:SS<sep>mmm``.

    `sep` is ``","`` for SRT and ``"."`` for VTT — the one byte that
    distinguishes the two formats' timestamps. Hours are not capped (they roll
    over past 24h); H/M/S are zero-padded to 2 digits and ms to 3.
    """
    if total_ms < 0:
        raise ValueError(f"timestamp must be non-negative, got {total_ms}")
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{ms:03d}"


def _validate_cues(cues: Sequence[Caption]) -> None:
    for i, cue in enumerate(cues):
        if cue.end_ms < cue.start_ms:
            raise ValueError(f"cue {i} has end_ms ({cue.end_ms}) < start_ms ({cue.start_ms})")


def format_srt(track: CaptionTrack) -> str:
    """Render a `CaptionTrack` as SubRip (`.srt`) text.

    SRT cue: a 1-based index line, a ``start --> end`` line using **comma**
    millisecond separators, the text, and a blank-line separator. Pure and
    deterministic.
    """
    _validate_cues(track.cues)
    blocks: list[str] = []
    for i, cue in enumerate(track.cues, start=1):
        start = _format_timestamp(cue.start_ms, sep=",")
        end = _format_timestamp(cue.end_ms, sep=",")
        blocks.append(f"{i}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def format_vtt(track: CaptionTrack) -> str:
    """Render a `CaptionTrack` as WebVTT (`.vtt`) text.

    WebVTT begins with a ``WEBVTT`` header line, then cues whose ``start --> end``
    lines use **period** millisecond separators (no index line is required).
    Pure and deterministic.
    """
    _validate_cues(track.cues)
    blocks: list[str] = ["WEBVTT\n"]
    for cue in track.cues:
        start = _format_timestamp(cue.start_ms, sep=".")
        end = _format_timestamp(cue.end_ms, sep=".")
        blocks.append(f"{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def _format_ass_timestamp(total_ms: int) -> str:
    """Render integer milliseconds as ASS ``H:MM:SS.cc`` (centiseconds).

    ASS timestamps differ from SRT/VTT: a **single**-digit (un-padded, but not
    capped) hour, and **centiseconds** (2 digits) rather than milliseconds.
    `_format_timestamp` cannot be reused — it emits 2-digit hours and 3-digit ms.

    ms->cc is **truncated**, not rounded (locked decision, ADR 0059): ``cc =
    (total_ms % 1000) // 10``. Truncation always yields 0-99 and so can never
    overflow into a second-carry; rounding could push e.g. 995 ms to ``round(99.5)
    = 100``, which would need carry logic and is silently wrong without it. The
    sub-10 ms loss is inaudible/invisible at caption granularity.
    """
    if total_ms < 0:
        raise ValueError(f"timestamp must be non-negative, got {total_ms}")
    centis = (total_ms % 1000) // 10
    total_seconds = total_ms // 1000
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _ass_colour(rgb_hex: str) -> str:
    """Convert a ``#RRGGBB`` hex colour to ASS ``&HAABBGGRR`` (opaque).

    ASS colours are **inverted-alpha, BGR-ordered**: the byte order is alpha,
    blue, green, red, and alpha ``00`` means fully **opaque** (``FF`` is fully
    transparent). Captions are always opaque, so alpha is fixed at ``00``. E.g.
    ``#123456`` (R=12 G=34 B=56) → ``&H00563412``.

    Requires **exactly one** leading ``#`` and **exactly six** hex digits (shared
    `_RGB_HEX` shape): ``123456`` (no ``#``), ``##123456`` (double ``#``),
    ``#12345`` (short), and ``#GGGGGG`` (non-hex) all raise. Do not use
    ``lstrip('#')`` — it silently accepts the ``#``-less and double-``#`` forms.
    """
    if not _RGB_HEX.match(rgb_hex):
        raise ValueError(f"colour must be `#RRGGBB` hex, got {rgb_hex!r}")
    s = rgb_hex[1:]
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _escape_ass_text(text: str) -> str:
    r"""Neutralize ASS override characters in cue text by **replacement**.

    In ASS, ``{`` opens an override block, ``}`` closes it, and ``\`` introduces
    an override/special (``\N`` newline, ``\h`` hard space). There is no reliable
    literal-brace escape — outside ``{}`` a backslash is passed through but the
    brace still opens a block, so ``\{`` does NOT neutralize it. We therefore
    **replace** these characters with safe lookalikes (fullwidth braces, a plain
    forward slash) rather than backslash-escaping; this also makes escape-ordering
    irrelevant. Newlines collapse to a space (cue segmentation owns line breaks).
    """
    return (
        text.replace("\\", "/")
        .replace("{", "｛")  # noqa: RUF001 (fullwidth brace is the deliberate safe replacement)
        .replace("}", "｝")  # noqa: RUF001 (fullwidth brace is the deliberate safe replacement)
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _format_karaoke_body(cue: Caption) -> str:
    r"""Emit a cue's word spans as sequential ``\kf`` karaoke syllables. Pure.

    ASS karaoke: each ``{\kf<dur>}`` opens a syllable whose following text
    sweep-fills from SecondaryColour to PrimaryColour over ``<dur>``
    **centiseconds**, syllables playing strictly in sequence from the Dialogue
    line's start. This maps the cue's absolute-ms word spans onto that
    sequential clock (ADR 0062):

    * Boundaries are **cue-relative centiseconds, truncated** (``// 10`` — the
      same locked ms→cs decision as `_format_ass_timestamp`, ADR 0059) and
      clamped monotonically into ``[0, cue span]``. Each emitted duration is a
      *difference of cumulative boundaries*, so the total telescopes to the
      last word's end boundary and can never exceed the cue span — the karaoke
      always fits the Dialogue line's visible window.
    * The leading offset (cue start → first word) and inter-word gaps become
      **empty-text** ``{\kf<gap>}`` spacer syllables (silence sweeps nothing);
      zero-length spacers are omitted.
    * Clamping — not raising — absorbs a word that starts before the cue or
      ends after it: forced-aligned word times and the pipeline's
      length-proportional cue boundaries are *independent* estimates, so mild
      overhang at cue seams is expected and must degrade, never fail a render.
      A word whose own span is inverted (``end_ms < start_ms``) is a caller
      bug and raises, exactly like the cue-level `_validate_cues`.
    * Word text passes through the same `_escape_ass_text` as cue text; words
      are joined by a trailing space attached to the preceding syllable. The
      word spans **are** the rendered text — ``cue.text`` is not re-emitted.
    """
    span_cs = (cue.end_ms - cue.start_ms) // 10
    parts: list[str] = []
    prev_cs = 0
    last = len(cue.words) - 1
    for i, word in enumerate(cue.words):
        if word.end_ms < word.start_ms:
            raise ValueError(
                f"cue word {i} ({word.text!r}) has end_ms ({word.end_ms}) "
                f"< start_ms ({word.start_ms})"
            )
        start_cs = min(max((word.start_ms - cue.start_ms) // 10, prev_cs), span_cs)
        end_cs = min(max((word.end_ms - cue.start_ms) // 10, start_cs), span_cs)
        if start_cs > prev_cs:
            parts.append(f"{{\\kf{start_cs - prev_cs}}}")
        text = _escape_ass_text(word.text)
        if i != last:
            text += " "
        parts.append(f"{{\\kf{end_cs - start_cs}}}{text}")
        prev_cs = end_cs
    return "".join(parts)


def format_ass(
    track: CaptionTrack,
    *,
    style: CaptionStyle,
    width: int,
    height: int,
) -> str:
    r"""Render a `CaptionTrack` as Advanced SubStation Alpha (`.ass`) text.

    The styled burned-in-caption format (ADR 0059): unlike SRT/VTT it carries a
    font, colours, an outline, alignment, margins, and a per-cue fade — the
    engagement-quality look short-form needs. ``width``/``height`` are the real
    output frame, written as ``PlayResX``/``PlayResY`` so the font size and
    margins scale to the actual resolution rather than a phantom 384x288 default.

    Layout:

    * ``[Script Info]`` pins the script type, the play resolution,
      ``ScaledBorderAndShadow: yes`` (outline scales with the frame), and a
      ``WrapStyle`` that prefers balanced wrapping.
    * ``[V4+ Styles]`` is one brand row built from `style`: colours converted to
      ASS ``&HAABBGGRR`` via `_ass_colour`, alignment 2 (bottom-centre), and
      ``MarginL``/``MarginR`` = ``round(margin_fraction * width)`` with a
      ``MarginV`` placing text in the bottom third.
    * ``[Events]`` is one ``Dialogue:`` per cue; each cue's text is prefixed with
      a cue-level ``{\fad(in,out)}`` override, then either the (escaped) cue text
      or — when the cue carries word timings — per-word karaoke syllables.

    Word-level karaoke (ADR 0062): a cue whose ``words`` list is non-empty is
    emitted as sequential per-word ``{\kf}`` sweep syllables (see
    `_format_karaoke_body`); a cue with **no** words is emitted exactly as the
    ADR 0059 cue-level form, so a track with no word timings anywhere renders
    **byte-identically** to the pre-karaoke output (graceful degrade; mixed
    tracks are fine per-cue). The ``{\fad}`` is deliberately **kept** on karaoke
    lines: ``\fad`` animates line *alpha* while ``\kf`` animates per-syllable
    *fill colour* — independent channels libass composes without conflict — and
    keeping it gives mixed tracks one uniform entrance/exit. The style row's
    SecondaryColour (the karaoke pre-highlight fill) is set from
    ``style.secondary_colour`` only when the track carries words; otherwise it
    stays equal to PrimaryColour, preserving the byte-stability above.

    Honesty (ADR 0059/0062): line count (≤2) is **not** guaranteed here —
    ``WrapStyle`` + margins bias toward it but real wrapping needs font metrics
    and upstream cue segmentation. The output — including the karaoke look and
    its timing feel — is never visually validated hermetically (no libass in
    CI); the unit tests assert the ASS *text* shape, and a real-render check is
    a last-mile follow-up.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width/height must be positive, got {width}x{height}")
    _validate_cues(track.cues)

    margin_lr = round(style.margin_fraction * width)
    # Bottom-third vertical margin: lift the (bottom-anchored, alignment 2) text
    # off the very edge into the lower third of the frame. A deterministic,
    # resolution-relative value (not pinned by any test; documented here).
    margin_v = round(height * 0.06)
    primary = _ass_colour(style.primary_colour)
    outline = _ass_colour(style.outline_colour)
    # BackColour (shadow) reuses the outline colour, fully opaque.
    back = outline
    # SecondaryColour is the karaoke pre-highlight fill: \kf sweeps FROM it TO
    # PrimaryColour, so it must differ from primary for the sweep to be
    # visible. Only a track that actually carries word timings gets the karaoke
    # secondary; a wordless track keeps SecondaryColour == PrimaryColour so its
    # output stays byte-identical to the ADR 0059 cue-fade format (libass uses
    # SecondaryColour only for karaoke, so this is purely text-stability).
    has_word_timings = any(cue.words for cue in track.cues)
    secondary = _ass_colour(style.secondary_colour) if has_word_timings else primary

    # One style row, fields in the exact _ASS_STYLE_FORMAT order (23 fields):
    # Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,
    # BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing,
    # Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV,
    # Encoding. Bold=-1 (ASS true), BorderStyle=1 (outline+shadow), Alignment=2.
    style_row = (
        f"Style: {_ASS_STYLE_NAME},{style.font_name},{style.font_size},"
        f"{primary},{secondary},{outline},{back},"
        f"-1,0,0,0,100,100,0,0,1,{style.outline_width:g},0,2,"
        f"{margin_lr},{margin_lr},{margin_v},1"
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        f"Format: {_ASS_STYLE_FORMAT}",
        style_row,
        "",
        "[Events]",
        f"Format: {_ASS_EVENT_FORMAT}",
    ]

    fade = f"{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
    for cue in track.cues:
        start = _format_ass_timestamp(cue.start_ms)
        end = _format_ass_timestamp(cue.end_ms)
        # Karaoke when the cue carries word timings; the exact ADR 0059
        # cue-level form otherwise (graceful per-cue degrade).
        body = _format_karaoke_body(cue) if cue.words else _escape_ass_text(cue.text)
        text = fade + body
        # Per-cue margins are 0,0,0 -> inherit the Style row's real margins.
        lines.append(f"Dialogue: 0,{start},{end},{_ASS_STYLE_NAME},0,0,0,,{text}")

    return "\n".join(lines) + "\n"


@runtime_checkable
class SubtitleService(Protocol):
    """Builds a `CaptionTrack` from narration segments and their timings.

    Synchronous: the deterministic path is CPU-only. `segments` and `timings`
    are parallel sequences (segment ``i`` spans ``timings[i]``).
    """

    name: str

    def build_track(
        self,
        *,
        segments: Sequence[str],
        timings: Sequence[tuple[int, int]],
    ) -> CaptionTrack: ...


class DeterministicSubtitleService:
    """A concrete, real `SubtitleService` (no external dependency).

    Zips narration `segments` with their `(start_ms, end_ms)` `timings` into a
    `CaptionTrack`. This is the layer's one shipping-real implementation — the
    seam is not hollow. Pair it with `format_srt` / `format_vtt` to emit caption
    files.
    """

    name = "deterministic"

    def build_track(
        self,
        *,
        segments: Sequence[str],
        timings: Sequence[tuple[int, int]],
    ) -> CaptionTrack:
        if len(segments) != len(timings):
            raise ValueError(
                f"segments ({len(segments)}) and timings ({len(timings)}) must be the same length"
            )
        cues = [
            Caption(start_ms=start, end_ms=end, text=text)
            for text, (start, end) in zip(segments, timings, strict=True)
        ]
        _validate_cues(cues)
        return CaptionTrack(cues=cues, produced_via=f"subtitles:{self.name}")
