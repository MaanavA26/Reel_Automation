"""Cross-Verification agent — turns chunk-local Evidence into cross-checked Verdicts.

An *agent* (judgment, CLAUDE.md §4): it reads a bounded cluster of related
`Evidence` and decides whether the sources corroborate, contradict, or only
singly support a canonical claim. It opens the Knowledge Reasoning band — the
first node that produces *inference* (`Verdict`) rather than source-grounded
*fact* (`Evidence`); the two live in separate substates so downstream bands can
never conflate them (CLAUDE.md §11).

The agent/tool split (CLAUDE.md §4): the deterministic `claim_blocking` tool
groups evidence into candidate clusters (the lexical floor, bounding the O(N²)
cross-product); this agent does the semantic ceiling — judging each cluster via
the model. The §11 evidence-vs-inference boundary is made **structural** twice
over:

1. The model references evidence only by *local index* into the cluster it was
   shown; code resolves those indices to real `Evidence` ids and validates them,
   so a `Verdict` can never cite evidence the model invented (the M7
   unknown-source guard, generalized).
2. ``CORROBORATED`` means **two or more distinct sources agree**, and the
   distinct-source count is *code-derived* from the resolved supporting
   evidence — never trusted to the model. A model that over-claims
   ``CORROBORATED`` on intra-source repetition is downgraded to
   ``SINGLE_SOURCE``. ``CONTRADICTED`` stays a genuine model judgment.

See ADR 0010.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.schemas.research_state import Evidence, SupportLevel, Verdict
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter
from app.services.reasoning.claim_blocking import build_claim_blocks

logger = logging.getLogger(__name__)


class VerificationError(RuntimeError):
    """Raised on empty evidence input, or when no verdict is produced from it."""


SYSTEM_PROMPT = (
    "You are a cross-verification analyst for a research engine. You are given a "
    "small cluster of factual claims, each extracted from a source and labelled "
    "with a local index [i] and its source id. Group the claims that assert the "
    "SAME fact, and for each such group emit one verdict: a single canonical "
    "claim that captures the shared fact, a confidence in [0,1] for how well the "
    "cluster supports it, and the local indices that SUPPORT it versus those "
    "that CONTRADICT it. Use only the provided claims — do not add outside "
    "knowledge. Reference claims only by their given local index. Mark "
    "support_level as 'contradicted' when supporting and contradicting claims "
    "coexist; otherwise judge it 'corroborated' or 'single_source' (the engine "
    "re-checks the distinct-source count, so prefer accuracy over optimism)."
)


class _VerdictDraft(BaseModel):
    """Model-output shape for one verdict (local indices only — no evidence ids)."""

    claim: str
    support_level: SupportLevel
    confidence: float = Field(ge=0.0, le=1.0)
    supporting: list[int] = Field(default_factory=list)
    contradicting: list[int] = Field(default_factory=list)


class _VerificationOutput(BaseModel):
    """Structured output of one per-cluster verification call.

    The model may split a candidate cluster into multiple verdicts (the blocker
    over-groups by lexical overlap; the model decides true co-reference). Every
    index is local to the cluster; all ids/provenance are code-attached.
    """

    verdicts: list[_VerdictDraft] = Field(default_factory=list)


class CrossVerificationAgent:
    """Cross-checks `Evidence` into `Verdict`s via the ``PLANNING``-role model.

    Reuses the ``PLANNING`` role (analytical reasoning, like source discovery)
    rather than minting a verification-specific role: a distinct `ModelRole` is
    added only when the policy actually routes it to a distinct model (ADR 0003),
    which it does not yet.
    """

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def verify(self, evidence: list[Evidence]) -> list[Verdict]:
        """Cross-check ``evidence`` into `Verdict`s, one model call per cluster.

        Raises `VerificationError` if ``evidence`` is empty (never advance on
        empty input — the band assumes M7 already raised on zero extraction), or
        if no verdict is produced from non-empty input (every cluster call
        failed). Per-cluster model failures are tolerated (skip + log).
        """
        if not evidence:
            raise VerificationError("cross-verification received no evidence")

        blocks = build_claim_blocks(evidence)
        model = self._router.for_role(ModelRole.PLANNING)
        verified_via = f"verification:{model.model}"

        verdicts: list[Verdict] = []
        for block in blocks:
            members = [evidence[i] for i in block]
            try:
                output = await model.complete_structured(
                    system=SYSTEM_PROMPT,
                    prompt=self._build_prompt(members),
                    schema=_VerificationOutput,
                )
            except Exception as exc:  # one bad cluster must not fail the band
                logger.warning("cross-verification: skipping cluster %s: %s", block, exc)
                continue
            for draft in output.verdicts:
                verdict = self._build_verdict(draft, members, verified_via)
                if verdict is not None:
                    verdicts.append(verdict)

        if not verdicts:
            raise VerificationError("cross-verification produced no verdicts from any cluster")
        return verdicts

    def _build_verdict(
        self,
        draft: _VerdictDraft,
        members: list[Evidence],
        verified_via: str,
    ) -> Verdict | None:
        """Resolve a model draft into a `Verdict` with code-attached provenance.

        Local indices are resolved against ``members`` and out-of-range indices
        are dropped + logged (the model cited evidence it was not shown). Returns
        ``None`` for a draft with no resolvable supporting evidence — a verdict
        must rest on at least one real evidence item.
        """
        supporting = self._resolve_ids(draft.supporting, members)
        contradicting = self._resolve_ids(draft.contradicting, members)
        if not supporting:
            logger.warning("cross-verification: dropping verdict with no valid supporting evidence")
            return None

        support_level = self._reconcile_support_level(draft, supporting, contradicting)
        return Verdict(
            claim=draft.claim,
            support_level=support_level,
            supporting_evidence_ids=[ev.id for ev in supporting],
            contradicting_evidence_ids=[ev.id for ev in contradicting],
            confidence=draft.confidence,
            verified_via=verified_via,
        )

    @staticmethod
    def _resolve_ids(local_indices: list[int], members: list[Evidence]) -> list[Evidence]:
        """Map cluster-local indices to `Evidence`, dropping out-of-range ones."""
        resolved: list[Evidence] = []
        seen: set[str] = set()
        for i in local_indices:
            if 0 <= i < len(members):
                ev = members[i]
                if ev.id not in seen:  # de-dup if the model lists an index twice
                    resolved.append(ev)
                    seen.add(ev.id)
            else:
                logger.warning("cross-verification: dropping out-of-range local index %s", i)
        return resolved

    @staticmethod
    def _reconcile_support_level(
        draft: _VerdictDraft,
        supporting: list[Evidence],
        contradicting: list[Evidence],
    ) -> SupportLevel:
        """Code-derive the structural support level; the model only proposes it.

        Two structural facts are code-checked, never trusted to the model:

        - ``CONTRADICTED`` requires at least one *resolved* contradicting
          evidence item. A model that labels a verdict ``CONTRADICTED`` while
          citing no valid contradicting evidence (none given, or all dropped as
          out-of-range) is downgraded — "sources conflict" with nothing listed
          as conflicting is the same silent-wrong class as fabricated support.
        - ``CORROBORATED`` requires **>=2 distinct sources** in the supporting
          set; intra-source repetition is downgraded to ``SINGLE_SOURCE``.

        Once those gates pass, the level falls out of the supporting evidence:
        >=2 distinct sources → ``CORROBORATED``, else ``SINGLE_SOURCE``.
        """
        if draft.support_level is SupportLevel.CONTRADICTED:
            if contradicting:
                return SupportLevel.CONTRADICTED
            logger.info(
                "cross-verification: downgrading CONTRADICTED (no valid contradicting evidence)"
            )
        distinct_sources = {ev.source_id for ev in supporting}
        if len(distinct_sources) >= 2:
            return SupportLevel.CORROBORATED
        if draft.support_level is SupportLevel.CORROBORATED:
            logger.info(
                "cross-verification: downgrading CORROBORATED -> SINGLE_SOURCE "
                "(supporting evidence spans %d distinct source(s))",
                len(distinct_sources),
            )
        return SupportLevel.SINGLE_SOURCE

    @staticmethod
    def _build_prompt(members: list[Evidence]) -> str:
        lines = "\n".join(
            f"[{i}] (source {ev.source_id}) {ev.claim}" for i, ev in enumerate(members)
        )
        return (
            f"Claim cluster:\n\n{lines}\n\n"
            "Emit verdicts grouping the claims that assert the same fact."
        )
