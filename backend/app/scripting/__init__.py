"""Short-form script + shot-list builder package (ADR 0038, 0061).

A deterministic *tool* (CLAUDE.md §4) that turns a Deep Research `CreatorPacket`
into a typed `ShortScript` — an ordered 4-beat retention arc
(HOOK → BUILD… → PAYOFF → LOOP) with advisory durations and visual-keyword cues —
ready for the Media Production layer. See `app/scripting/builder.py` and ADR 0061
(superseding the hook → body → CTA shape of ADR 0038).

``DEFAULT_CTA_TEXT`` is a deprecated alias kept exported so downstream imports do
not break; prefer ``DEFAULT_LOOP_TEXT`` (ADR 0061).
"""

from __future__ import annotations

from app.scripting.builder import (
    DEFAULT_CTA_TEXT,
    DEFAULT_LOOP_TEXT,
    SHORTS_CEILING_MS,
    SHORTS_FLOOR_MS,
    WORDS_PER_MINUTE,
    ScriptBuilder,
    ScriptBuilderError,
)
from app.scripting.schemas import BeatRole, ScriptBeat, ShortScript

__all__ = [
    "DEFAULT_CTA_TEXT",
    "DEFAULT_LOOP_TEXT",
    "SHORTS_CEILING_MS",
    "SHORTS_FLOOR_MS",
    "WORDS_PER_MINUTE",
    "BeatRole",
    "ScriptBeat",
    "ScriptBuilder",
    "ScriptBuilderError",
    "ShortScript",
]
