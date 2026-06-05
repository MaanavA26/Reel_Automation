"""Short-form script + shot-list builder — a deterministic structuring tool.

`ScriptBuilder` takes a Deep Research `CreatorPacket` (the band-D handoff
artifact, CLAUDE.md §5.4) and produces a typed `ShortScript`: an ordered list of
`ScriptBeat`s (hook → body beats → CTA), each with voiceover text, an advisory
duration estimate, and a visual-keyword cue for the downstream B-roll retrieval
seam.

Tool, not agent (CLAUDE.md §4)
------------------------------
Composing a script from a packet is the borderline §4 case: the *creative
wording* (hooks, the narrative arc) is judgment and already happened upstream in
the Short-Form Content Strategist (`CreatorPacketAgent`, M12). This tool only
does the **deterministic structuring** — selecting a hook + narrative, splitting
the arc into ordered beats, estimating durations, deriving visual cues, and
appending a claim-free CTA. There is no LLM call and no judgment about *what* to
say. Creative LLM rewriting of the structured beats is a documented future
enhancement (ADR 0038), not v1.

§11 honesty (made structural)
-----------------------------
The builder never smooths a disputed claim. A beat's grounding (``finding_ids``)
and its ``disputed`` flag are **code-derived** from the packet's `KeyFact` map
(``finding_id → disputed``), and the relevant `CreatorWarning`s are carried
forward verbatim onto the `ShortScript` (the non-omittable posture inherited
from the `CreatorPacket`). The narrative carries a single whole-arc
``finding_ids`` list (there is no per-line attribution in the packet), so every
body beat is flagged at the element level — honest about *that the arc rests on*
a disputed finding without fabricating per-line precision.

Selection mirrors `MediaPipeline`: ``hook_index`` / ``narrative_index`` are
deterministic (ranking would be judgment, §4); out-of-range or missing elements
raise `ScriptBuilderError`, mirroring `MediaPipelineError`.
"""

from __future__ import annotations

import re

from app.schemas.research_state import CreatorPacket, CreatorWarning, HookIdea, NarrativeOption
from app.scripting.schemas import BeatRole, ScriptBeat, ShortScript

# Shorts ceiling: vertical short-form (YouTube Shorts / Instagram Reels) caps at
# 60 seconds. The builder targets at most this and flags overflow (ADR 0038).
SHORTS_CEILING_MS = 60_000

# Words-per-minute used for the deterministic duration estimate. ~150 wpm is a
# common conversational narration pace; it is advisory only — `MediaPipeline`
# does the real timing post-TTS, so this need not be exact, only stable.
WORDS_PER_MINUTE = 150

# The default closing call-to-action. Claim-free structural scaffolding: it
# asserts nothing about the topic, so it carries no grounding and is never
# flagged disputed (ADR 0038). Caller-overridable via `ScriptBuilder(cta_text=)`.
DEFAULT_CTA_TEXT = "Follow for more."

# A small English stopword set for visual-keyword extraction. Deliberately tiny
# and deterministic — the keyword is only a *seed* for the `VisualProvider`
# retrieval seam (ADR 0024), which owns retrieval quality. LLM keyword
# refinement is a documented future enhancement (ADR 0038).
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


class ScriptBuilderError(RuntimeError):
    """Raised when the packet yields no buildable script.

    Mirrors `MediaPipelineError`: the packet must have a selectable hook and a
    selectable narrative with at least one narratable beat. Selection
    (``hook_index`` / ``narrative_index``) out of range, or a narrative whose
    ``script_outline`` has no non-blank line, raises rather than emitting an
    empty or hook-only script.
    """


def _extract_visual_keyword(text: str) -> str:
    """Derive a deterministic B-roll seed keyword from a beat's text.

    Strips stopwords (case-insensitively) and joins up to three of the remaining
    content tokens in their original order. Falls back to the first token, then
    to a generic ``"abstract background"`` seed, so a beat always has a cue. The
    keyword is only a *seed* for the `VisualProvider`; retrieval quality lives
    there (ADR 0024).
    """
    tokens = _WORD_RE.findall(text)
    content = [t for t in tokens if t.lower() not in _STOPWORDS]
    chosen = content[:3] or tokens[:1]
    return " ".join(chosen).lower() if chosen else "abstract background"


class ScriptBuilder:
    """Turns a `CreatorPacket` into a typed `ShortScript` (pure, deterministic).

    No injected dependencies and no I/O — the whole tool is a pure function of
    the packet plus the selection indices, which makes it fully unit-testable
    (CLAUDE.md §4/§9.3). ``cta_text`` and ``words_per_minute`` are constructor
    knobs with safe defaults so the common path needs no configuration.
    """

    name = "scriptbuilder"

    def __init__(
        self,
        *,
        cta_text: str = DEFAULT_CTA_TEXT,
        words_per_minute: int = WORDS_PER_MINUTE,
        shorts_ceiling_ms: int = SHORTS_CEILING_MS,
    ) -> None:
        if words_per_minute <= 0:
            raise ValueError(f"words_per_minute must be positive, got {words_per_minute}")
        if shorts_ceiling_ms <= 0:
            raise ValueError(f"shorts_ceiling_ms must be positive, got {shorts_ceiling_ms}")
        self._cta_text = cta_text
        self._wpm = words_per_minute
        self._ceiling_ms = shorts_ceiling_ms

    def build(
        self,
        packet: CreatorPacket,
        *,
        hook_index: int = 0,
        narrative_index: int = 0,
    ) -> ShortScript:
        """Build a `ShortScript` from the packet's chosen hook + narrative.

        Selects ``packet.hooks[hook_index]`` and
        ``packet.narratives[narrative_index]`` (deterministic — ranking would be
        judgment, §4), assembles a hook beat → one body beat per non-blank
        ``script_outline`` line → a claim-free CTA beat, estimates each beat's
        duration, derives a visual-keyword cue, and carries forward the relevant
        `CreatorWarning`s. Raises `ScriptBuilderError` if the hook/narrative
        index is missing/out of range or the narrative has no narratable beat.
        """
        hook = self._select_hook(packet, hook_index)
        narrative = self._select_narrative(packet, narrative_index)

        body_lines = _split_into_beats(narrative.script_outline)
        if not body_lines:
            raise ScriptBuilderError(
                f"narrative {narrative.title!r} produced no narratable script beats"
            )

        disputed_by_finding = {kf.finding_id: kf.disputed for kf in packet.key_facts}

        beats: list[ScriptBeat] = [
            self._make_beat(BeatRole.HOOK, hook.text, hook.finding_ids, disputed_by_finding)
        ]
        beats.extend(
            self._make_beat(BeatRole.BODY, line, narrative.finding_ids, disputed_by_finding)
            for line in body_lines
        )
        # The CTA is claim-free structural scaffolding: no grounding, never
        # disputed (advisor guidance / ADR 0038). It is exempt from the
        # finding-id flagging the topical beats undergo.
        beats.append(
            ScriptBeat(
                role=BeatRole.CTA,
                text=self._cta_text,
                estimated_duration_ms=self._estimate(self._cta_text),
                visual_keyword=_extract_visual_keyword(self._cta_text),
                disputed=False,
                finding_ids=[],
            )
        )

        total_estimated_ms = sum(beat.estimated_duration_ms for beat in beats)
        used_finding_ids = {fid for beat in beats for fid in beat.finding_ids}

        return ShortScript(
            source_packet_id=packet.id,
            narrative_title=narrative.title,
            beats=beats,
            total_estimated_ms=total_estimated_ms,
            target_duration_ms=min(total_estimated_ms, self._ceiling_ms),
            exceeds_shorts_ceiling=total_estimated_ms > self._ceiling_ms,
            warnings=_relevant_warnings(packet.warnings, used_finding_ids),
            built_via=f"scripting:{self.name}",
        )

    def _make_beat(
        self,
        role: BeatRole,
        text: str,
        finding_ids: list[str],
        disputed_by_finding: dict[str, bool],
    ) -> ScriptBeat:
        """Build one topical (hook/body) beat with code-derived grounding.

        ``disputed`` is True iff any cited finding is disputed per the packet's
        `KeyFact` map; unknown finding ids (defensive — should not occur for a
        well-formed packet) are treated as not-disputed. Grounding is the
        element's whole-arc ``finding_ids`` (de-duplicated, order preserved).
        """
        ids: list[str] = []
        for fid in finding_ids:
            if fid not in ids:
                ids.append(fid)
        disputed = any(disputed_by_finding.get(fid, False) for fid in ids)
        return ScriptBeat(
            role=role,
            text=text,
            estimated_duration_ms=self._estimate(text),
            visual_keyword=_extract_visual_keyword(text),
            disputed=disputed,
            finding_ids=ids,
        )

    def _estimate(self, text: str) -> int:
        words = max(len(_WORD_RE.findall(text)), 1) if text.strip() else 0
        return round(words / self._wpm * 60_000)

    @staticmethod
    def _select_hook(packet: CreatorPacket, index: int) -> HookIdea:
        if not packet.hooks:
            raise ScriptBuilderError(f"creator packet {packet.id} has no hooks to open with")
        if not 0 <= index < len(packet.hooks):
            raise ScriptBuilderError(
                f"hook_index {index} out of range "
                f"(packet {packet.id} has {len(packet.hooks)} hooks)"
            )
        return packet.hooks[index]

    @staticmethod
    def _select_narrative(packet: CreatorPacket, index: int) -> NarrativeOption:
        if not packet.narratives:
            raise ScriptBuilderError(
                f"creator packet {packet.id} has no narrative options to script"
            )
        if not 0 <= index < len(packet.narratives):
            raise ScriptBuilderError(
                f"narrative_index {index} out of range "
                f"(packet {packet.id} has {len(packet.narratives)} narratives)"
            )
        return packet.narratives[index]


def _split_into_beats(script_outline: str) -> list[str]:
    """Split a narrative's ``script_outline`` into non-blank narration beats.

    Line-oriented and deterministic — one body beat per non-blank line, matching
    the beat-by-beat shape the strategist authors and the exact split
    `MediaPipeline._split_into_beats` performs downstream, so one beat = one
    narration segment = one caption cue stays a trivial wiring step.
    """
    return [stripped for line in script_outline.splitlines() if (stripped := line.strip())]


def _relevant_warnings(
    warnings: list[CreatorWarning], used_finding_ids: set[str]
) -> list[CreatorWarning]:
    """Carry forward the packet warnings that touch a finding used by the script.

    A `CreatorWarning` travels by shared ``finding_ids`` (ADR 0018), so a
    warning is relevant iff any of its findings appears in a beat. The warnings
    are kept *verbatim* (not re-derived) — the non-omittable §11 posture carried
    one layer downstream onto the script.
    """
    return [w for w in warnings if any(fid in used_finding_ids for fid in w.finding_ids)]
