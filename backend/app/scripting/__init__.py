"""Short-form script + shot-list builder package (ADR 0038).

A deterministic *tool* (CLAUDE.md §4) that turns a Deep Research `CreatorPacket`
into a typed `ShortScript` — an ordered hook → body → CTA beat list with
advisory durations and visual-keyword cues — ready for the Media Production
layer. See `app/scripting/builder.py` and ADR 0038.
"""

from __future__ import annotations

from app.scripting.builder import (
    DEFAULT_CTA_TEXT,
    SHORTS_CEILING_MS,
    WORDS_PER_MINUTE,
    ScriptBuilder,
    ScriptBuilderError,
)
from app.scripting.schemas import BeatRole, ScriptBeat, ShortScript

__all__ = [
    "DEFAULT_CTA_TEXT",
    "SHORTS_CEILING_MS",
    "WORDS_PER_MINUTE",
    "BeatRole",
    "ScriptBeat",
    "ScriptBuilder",
    "ScriptBuilderError",
    "ShortScript",
]
