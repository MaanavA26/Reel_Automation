"""Typed DTOs for the short-form script + shot-list builder (ADR 0038, 0061).

These are the structured outputs of the `ScriptBuilder` *tool* (CLAUDE.md §4):
a `ShortScript` is an ordered list of `ScriptBeat`s following the 4-beat
retention arc HOOK → BUILD → PAYOFF → LOOP (ADR 0061, superseding the old
hook → body → CTA shape), each carrying voiceover text, an advisory duration
estimate, and a visual-keyword cue for the downstream B-roll retrieval seam
(`VisualProvider`, ADR 0024).

Strict (`extra='forbid'`) with an id-prefixed `ShortScript` and id-less
`ScriptBeat`s — the script is a first-class artifact, a beat is its sub-unit,
mirroring the M12 `CreatorPacket` / `HookIdea` shape. `_gen_id` is a small local
copy of the `research_state` helper (same prefix + 64-bit-hex convention), kept
local so this package neither imports a private symbol nor couples to the schema
beyond the read-only `CaveatKind` / `CreatorWarning` it carries forward.

The §11 honesty boundary made structural
----------------------------------------
A `ScriptBeat` carries a code-derived ``disputed`` flag and the (single, whole-
arc) ``finding_ids`` the beat rests on; the `ShortScript` carries the relevant
`CreatorWarning`s forward verbatim (the non-omittable posture inherited from the
`CreatorPacket`). So a disputed or thinly-supported claim is *flagged on the
beat and kept as a warning* — never silently smoothed into polished voiceover.
The LOOP beat is the one structural exception: it asserts nothing about the
topic (no ``finding_ids``, never ``disputed``).

Structure ≠ craft (ADR 0061)
----------------------------
Labelling the beats HOOK/BUILD/PAYOFF/LOOP is deterministic *structuring*, not
*writing*. These DTOs make the retention arc explicit and machine-checkable;
they do not make the hook stickier, the payoff sharper, or the loop seam back to
the opener — that is the upstream writing model's job (a separate future step).
"""

from __future__ import annotations

import secrets
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.research_state import CreatorWarning

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8); hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous. Same scheme as ADR 0001's
    # `research_state._gen_id`, copied (not imported) to keep the package's
    # coupling to the Deep Research schema read-only and minimal (ADR 0038).
    return f"{prefix}_{secrets.token_hex(8)}"


class BeatRole(StrEnum):
    """The structural role of a `ScriptBeat` within a short-form arc.

    The ordering of a `ShortScript` is fixed by construction (ADR 0061): exactly
    one ``HOOK`` first, then one or more ``BUILD`` beats, then exactly one
    ``PAYOFF``, then exactly one ``LOOP`` last. (For a single-line narrative
    there are no ``BUILD`` beats: ``HOOK → PAYOFF → LOOP``.)

    ``BODY`` and ``CTA`` are **deprecated** (ADR 0061 supersedes 0038): the
    builder no longer emits them (``BODY`` split into ``BUILD``/``PAYOFF``,
    ``CTA`` renamed ``LOOP``). They are **retained, not removed**, because
    `StrEnum` values may already be serialized in persisted `ShortScript`
    records; deleting them would break deserialization of those old records.
    Do not emit them from new code.
    """

    HOOK = "hook"  # the scroll-stopping opener
    BUILD = "build"  # a rising narration beat that develops the arc
    PAYOFF = "payoff"  # the distinct final-act resolution (last topical beat)
    LOOP = "loop"  # the closing re-hook that seams back (claim-free scaffolding)

    # --- deprecated (ADR 0061, no longer emitted — kept for deserialization) ---
    BODY = "body"  # deprecated: superseded by BUILD/PAYOFF
    CTA = "cta"  # deprecated: superseded by LOOP


class ScriptBeat(BaseModel):
    """One ordered beat of a short-form voiceover script (ADR 0038).

    Carries the voiceover ``text`` (a single clean line — no internal newlines,
    so it maps 1:1 onto a `MediaPipeline` narration segment / caption cue), an
    advisory ``estimated_duration_ms`` (a deterministic words-per-minute
    estimate; the *real* timing is allocated post-TTS by `MediaPipeline`), and a
    ``visual_keyword`` cue for the B-roll retrieval seam.

    ``disputed`` and ``finding_ids`` are **code-derived** from the source packet
    (never authored here): ``finding_ids`` are the element's whole-arc grounding
    and ``disputed`` is True iff any cited finding is disputed. The LOOP beat
    claims nothing about the topic, so it always has empty ``finding_ids`` and
    ``disputed=False``.
    """

    model_config = _STRICT

    role: BeatRole
    text: str
    estimated_duration_ms: int = Field(ge=0)
    visual_keyword: str
    disputed: bool = False
    finding_ids: list[str] = Field(default_factory=list)


class ShortScript(BaseModel):
    """A retention-optimized short-form voiceover script + shot list (ADR 0061).

    The deterministic-structuring output of `ScriptBuilder`: an ordered
    ``beats`` list following the 4-beat retention arc
    (HOOK → BUILD… → PAYOFF → LOOP), a re-join key back to the source packet
    (``source_packet_id``, mirroring `MediaPlan.source_packet_id` /
    `CreatorPacket.report_id`), the chosen ``narrative_title``, and the §11
    honesty carry-forward — the relevant `CreatorWarning`s kept verbatim.

    Timing fields (all advisory — `MediaPipeline` does the real post-TTS
    allocation) form a **length band**: the builder flags out-of-band scripts,
    it never pads or scales — the per-beat estimates stay honest WPM numbers, and
    both under- and over-length are surfaced for the caller (or the editorial
    loop) to act on (ADR 0061).

    - ``total_estimated_ms`` — the honest sum of the beats' WPM estimates.
    - ``target_duration_ms`` — ``min(total_estimated_ms, SHORTS_CEILING_MS)``,
      the Shorts-suitable target the media pipeline aims at.
    - ``exceeds_shorts_ceiling`` — True iff ``total_estimated_ms`` exceeds the
      60s Shorts ceiling (band upper bound).
    - ``below_shorts_floor`` — True iff ``total_estimated_ms`` is *below* the
      Shorts floor (``SHORTS_FLOOR_MS``, default 45s; band lower bound). Too-thin
      scripts under-retain against the QC rubric, so this is flagged, not
      fabricated up to length.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("scr"))
    source_packet_id: str
    narrative_title: str
    beats: list[ScriptBeat] = Field(default_factory=list)
    total_estimated_ms: int = Field(ge=0)
    target_duration_ms: int = Field(ge=0)
    exceeds_shorts_ceiling: bool = False
    below_shorts_floor: bool = False
    warnings: list[CreatorWarning] = Field(default_factory=list)
    built_via: str
