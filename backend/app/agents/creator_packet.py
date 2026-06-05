"""Creator-packet agent — the Short-Form Content Strategist (M12).

An *agent* (judgment, CLAUDE.md §4 / §5.6): given the published `Report` and the
synthesized `Finding`s it authors short-form creative material — hook ideas,
content angles, and short narrative options — for a faceless short-form video. It
closes the Research Publishing band (§5.5 band D), producing the creator-ready
handoff artifact the downstream media layer consumes (§5.4).

The agent/tool split, held one layer past M11: the *creative prose* is judgment
(this agent); the *key facts* and the *unsafe/unverified-claim warnings* are
deterministic and code-owned, so a punchy hook can never quietly rest on a
disputed or single-source finding without the warning surfacing:

1. The model references findings only by *local index* (``F#``) into the numbered
   list it was shown; code resolves them to real `Finding` ids (out-of-range
   dropped), and a creative element resolving to zero real findings is dropped. A
   *single* model index space — the `Report` is given as prose *context*, not a
   second index space — so the M9 two-index hazard cannot arise (ADR 0017's
   single-space choice, carried forward).
2. ``key_facts`` are projected in code straight from the findings (statement +
   code-derived grounding); ``warnings`` are derived from the *full* findings set
   (`derive_creator_warnings`), independent of which findings the elements cite.
   The model authors neither — they are non-omittable by construction, and a
   warning ties to a hook/angle/narrative by shared ``finding_ids``.

Single ``LONG_CONTEXT`` model call over the already-reduced report + findings
(short-form ideation is long-form summarization — the same honest role label as
M9/M11; reuse, not a new role, per ADR 0003). See ADR 0018.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.schemas.research_state import (
    ContentAngle,
    CreatorPacket,
    Finding,
    HookIdea,
    KeyFact,
    NarrativeOption,
    Report,
)
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter
from app.services.publishing.warnings import derive_creator_warnings

logger = logging.getLogger(__name__)


class CreatorPacketError(RuntimeError):
    """Raised on empty findings input, or when no creative element survives."""


SYSTEM_PROMPT = (
    "You are a short-form content strategist for faceless YouTube Shorts and "
    "Instagram Reels. You are given a research report (title, abstract, sections) "
    "and the synthesized findings (numbered F0, F1, …), each tagged with its "
    "support strength (whether it is disputed or single-source). Produce a creator "
    "packet for a short vertical video: scroll-stopping hook ideas, distinct "
    "content angles (each with a one-line rationale), and a few short narrative "
    "options (each a title + a beat-by-beat script outline). Ground every idea in "
    "the findings and cite the findings it draws on by their F-number. Reflect "
    "disputed or single-source support honestly — never build a hook on a "
    "contradicted claim as if it were settled. Use only the provided findings; "
    "reference them only by their F-number. Do not write a key-facts list or any "
    "safety/accuracy warnings — the engine derives those from the grounded data."
)


class _HookDraft(BaseModel):
    """Model-output shape for one hook (local indices only — no ids)."""

    text: str
    findings: list[int] = Field(default_factory=list)  # F# → synthesis.findings


class _AngleDraft(BaseModel):
    """Model-output shape for one content angle (local indices only)."""

    angle: str
    rationale: str
    findings: list[int] = Field(default_factory=list)


class _NarrativeDraft(BaseModel):
    """Model-output shape for one narrative option (local indices only)."""

    title: str
    script_outline: str
    findings: list[int] = Field(default_factory=list)


class _PacketOutput(BaseModel):
    """Structured output of the single packet call (creative prose + local indices)."""

    hooks: list[_HookDraft] = Field(default_factory=list)
    angles: list[_AngleDraft] = Field(default_factory=list)
    narratives: list[_NarrativeDraft] = Field(default_factory=list)


class CreatorPacketAgent:
    """Composes a `CreatorPacket` from a `Report` + findings via ``LONG_CONTEXT``."""

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def generate(self, report: Report, findings: list[Finding]) -> CreatorPacket:
        """Generate the `CreatorPacket` from the published report + the findings.

        Raises `CreatorPacketError` if there are no findings (never publish on
        empty — report generation raises upstream, so this is a defensive wiring
        guard) or if no creative element survives id-resolution (the call failed
        or every element cited only unresolvable findings). A thin / heavily-warned
        packet is **not** a failure: it ships with prominent code-derived warnings.
        """
        if not findings:
            raise CreatorPacketError("creator packet generation received no findings")

        model = self._router.for_role(ModelRole.LONG_CONTEXT)
        published_via = f"packet:{model.model}"

        output = await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(report, findings),
            schema=_PacketOutput,
        )

        hooks = self._resolve_hooks(output.hooks, findings)
        angles = self._resolve_angles(output.angles, findings)
        narratives = self._resolve_narratives(output.narratives, findings)
        if not hooks and not angles and not narratives:
            raise CreatorPacketError("creator packet generation produced no resolvable elements")

        # key_facts + warnings are code-derived over the FULL findings set
        # (not the cited subset) so an uncited disputed finding still surfaces a
        # warning; the model authors neither (ADR 0018).
        key_facts = [
            KeyFact(
                statement=f.statement,
                finding_id=f.id,
                disputed=f.disputed,
                weakest_support=f.weakest_support,
            )
            for f in findings
        ]
        warnings = derive_creator_warnings(findings)

        return CreatorPacket(
            report_id=report.id,
            hooks=hooks,
            angles=angles,
            narratives=narratives,
            key_facts=key_facts,
            warnings=warnings,
            published_via=published_via,
        )

    @classmethod
    def _resolve_hooks(cls, drafts: list[_HookDraft], findings: list[Finding]) -> list[HookIdea]:
        hooks: list[HookIdea] = []
        for draft in drafts:
            ids = cls._resolve_finding_ids(draft.findings, findings, "hook")
            if ids:
                hooks.append(HookIdea(text=draft.text, finding_ids=ids))
        return hooks

    @classmethod
    def _resolve_angles(
        cls, drafts: list[_AngleDraft], findings: list[Finding]
    ) -> list[ContentAngle]:
        angles: list[ContentAngle] = []
        for draft in drafts:
            ids = cls._resolve_finding_ids(draft.findings, findings, "angle")
            if ids:
                angles.append(
                    ContentAngle(angle=draft.angle, rationale=draft.rationale, finding_ids=ids)
                )
        return angles

    @classmethod
    def _resolve_narratives(
        cls, drafts: list[_NarrativeDraft], findings: list[Finding]
    ) -> list[NarrativeOption]:
        narratives: list[NarrativeOption] = []
        for draft in drafts:
            ids = cls._resolve_finding_ids(draft.findings, findings, "narrative")
            if ids:
                narratives.append(
                    NarrativeOption(
                        title=draft.title, script_outline=draft.script_outline, finding_ids=ids
                    )
                )
        return narratives

    @staticmethod
    def _resolve_finding_ids(
        local_indices: list[int], findings: list[Finding], element: str
    ) -> list[str]:
        """Resolve a creative element's local finding indices to real ids.

        Out-of-range indices are dropped + logged; the caller drops the element
        entirely when this returns empty (the M9/M11 drop-empty guard — a creative
        element must rest on ≥1 real finding). De-duplicates while preserving order.
        """
        resolved: list[str] = []
        for i in local_indices:
            if 0 <= i < len(findings):
                fid = findings[i].id
                if fid not in resolved:
                    resolved.append(fid)
            else:
                logger.warning("packet: dropping out-of-range finding index %s in %s", i, element)
        return resolved

    @staticmethod
    def _build_prompt(report: Report, findings: list[Finding]) -> str:
        section_lines = "\n".join(f"- {s.heading}: {s.narrative}" for s in report.sections)
        finding_lines = "\n".join(
            f"[F{i}] ({'disputed' if f.disputed else f.weakest_support.value}) {f.statement}"
            for i, f in enumerate(findings)
        )
        return (
            f"Report title:\n{report.title}\n\n"
            f"Report abstract:\n{report.abstract}\n\n"
            f"Report sections:\n{section_lines or '(none)'}\n\n"
            f"Synthesized findings:\n{finding_lines}\n\n"
            "Produce the creator packet (hooks, angles, narrative options) grounded "
            "in the findings, referencing them by F-number."
        )
