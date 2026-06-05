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
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("vid"))
    video_uri: str
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    produced_via: str
