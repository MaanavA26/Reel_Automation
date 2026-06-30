"""Typed artifact DTOs for the Media Production layer.

These are the structured outputs the media *tools* (TTS, subtitles,
composition) produce — the media-side analogues of the Deep Research `Source` /
`Evidence` provenance objects. Like those, each artifact carries a typed,
required `produced_via` provenance string (symmetric with `Source.discovered_via`
and `Evidence.extracted_via`; ADR 0006), so an artifact always records *which
tool* made it (e.g. ``"tts:fake"``, ``"tts:elevenlabs"``, ``"subtitles:deterministic"``,
``"composition:fake"``, ``"composition:ffmpeg"``). All models are strict
(`extra='forbid'`) with id-prefixed opaque ids, mirroring the repo's scheme.

`_gen_id` is intentionally a small local copy of the `research_state` helper:
the Media layer is decoupled from the Deep Research schema (it imports nothing
from `app.schemas`), which coheres with the deferred creator-packet → media
handoff contract (ADR 0019). The two are kept in sync by the shared, documented
convention (prefix + 64 bits hex), not by a cross-layer import of a private
symbol.
"""

from __future__ import annotations

import secrets

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8); hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous. Same scheme as ADR 0001's
    # `research_state._gen_id`, copied (not imported) to keep the layer
    # decoupled — see this module's docstring and ADR 0019.
    return f"{prefix}_{secrets.token_hex(8)}"


class SynthesizedSpeech(BaseModel):
    """An audio artifact produced from text by a `TTSProvider`.

    A lightweight descriptor, not the audio bytes: it points at where the audio
    lives (`audio_uri`) and carries the metadata a downstream composition step
    needs (duration, voice). The bytes themselves are an opaque blob owned by
    storage; the media layer traffics in descriptors so it stays storage- and
    provider-neutral.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("aud"))
    audio_uri: str
    duration_ms: int = Field(ge=0)
    voice: str
    produced_via: str


class Caption(BaseModel):
    """A single timed caption cue (one subtitle line group).

    Times are **integer milliseconds** (not float seconds) to keep the
    ms→timestamp formatting exact and rounding-free. ``end_ms >= start_ms`` is
    enforced by the formatter, not the DTO, so partially-built tracks remain
    representable.
    """

    model_config = _STRICT

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str


class CaptionTrack(BaseModel):
    """An ordered set of caption cues produced by a `SubtitleService`.

    The structured, format-agnostic representation; rendering to SRT/VTT text is
    a separate pure formatting step (`subtitles.base.format_srt` / `format_vtt`).
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("sub"))
    cues: list[Caption] = Field(default_factory=list)
    produced_via: str


class RenderedVideo(BaseModel):
    """A rendered video artifact produced by a `CompositionService`.

    Descriptor (not the video bytes): the FFmpeg/composition step assembles
    audio + captions + visuals into a single vertical short-form file and
    returns where it lives plus the metadata a publishing step needs.

    ``edit_list`` records the rendered cut structure as ordered, non-overlapping
    ``(start_ms, end_ms)`` visual segments (the deterministic per-visual equal
    slices the composition step lays out — see `FfmpegCompositionService.render`).
    It is the hermetic, optical-flow-free source the QC gate's ``CUT_RHYTHM``
    check ranges over (ADR 0060): N visuals → N segments tiling ``[0, duration_ms]``;
    a single visual → one full-length segment (zero cuts). Defaulted to an empty
    list so existing constructions (the fake renderer, publishing tests) stay
    valid; an empty list means "cut structure not recorded", which the QC gate
    treats as a SKIPPED ``CUT_RHYTHM`` rather than a (false) pass.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("vid"))
    video_uri: str
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    produced_via: str
    edit_list: list[tuple[int, int]] = Field(default_factory=list)


class CaptionStyle(BaseModel):
    """Brand styling for burned-in captions, consumed by `format_ass` (ADR 0059).

    A strict (`extra="forbid"`) value object carrying the visual parameters of a
    single ASS ``[V4+ Styles]`` row plus a per-cue fade. Colours are stored as
    ``#RRGGBB`` hex (the format a designer/brand kit speaks) and converted to
    ASS's inverted ``&HAABBGGRR`` form by the formatter — never stored in the
    wire format. Defaults are the project's caption brand: a bold sans face,
    large for mobile legibility, white fill with a heavy dark outline, a short
    symmetric fade, and a 10% left/right safe-margin so text never kisses the
    frame edge.

    This object describes **cue-level** styling only: a single fade per cue. It
    does NOT model word-level karaoke ("animated captions"); that is a separate
    future step (ADR 0059 §D2).
    """

    model_config = _STRICT

    font_name: str = "Arial"
    font_size: int = Field(default=72, gt=0)
    # #RRGGBB hex; converted to ASS &HAABBGGRR by format_ass.
    primary_colour: str = "#FFFFFF"
    outline_colour: str = "#000000"
    outline_width: float = Field(default=3.0, ge=0)
    fade_in_ms: int = Field(default=120, ge=0)
    fade_out_ms: int = Field(default=120, ge=0)
    # Left/right safe-area inset as a fraction of frame width (0.10 = 10%).
    margin_fraction: float = Field(default=0.10, ge=0, lt=0.5)


# Module-level default so callers can take a `CaptionStyle` parameter without a
# function-call-in-default-argument (ruff B008) and so the Protocol / ffmpeg /
# fake render signatures share one canonical default object (ADR 0059).
DEFAULT_CAPTION_STYLE = CaptionStyle()
