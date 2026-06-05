"""Evidence Extraction agent — turns chunks into grounded Evidence.

An *agent* (judgment, CLAUDE.md §4): it reads a chunk and extracts the factual
claims that chunk supports, each with a confidence score, via the ``EXTRACTION``
model role. The §11 evidence-vs-inference boundary is made **structural**: the
model authors only the ``claim`` and ``confidence``; the provenance snapshot
(``source_id``, ``source_url``, ``chunk_id``, ``chunk_text``) is *code-attached*
from the real `Chunk`/`Source` — never the model — so a claim can never be
misattributed to a source it did not come from. Each chunk is extracted in
isolation (one model call sees only that chunk's text), bounding hallucination
to chunk-supported claims. Whether a claim is *truly* entailed (vs corroborated
or contradicted across sources) is the Cross-Verification agent's job (M8). See
ADR 0009.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.schemas.research_state import Chunk, Evidence, Source
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class ExtractionError(RuntimeError):
    """Raised when extraction yields no evidence, or a chunk references an unknown source."""


SYSTEM_PROMPT = (
    "You are an evidence-extraction specialist. Given a single text chunk, extract the "
    "factual claims it directly supports, each with a confidence in [0,1] for how "
    "strongly THIS chunk supports it. Use only the provided chunk text — do not use "
    "outside knowledge and do not infer beyond what the text states. If the chunk "
    "supports no factual claims (e.g. navigation or boilerplate), return an empty list."
)


class _ExtractedClaim(BaseModel):
    """Model-output shape for one claim (no ids/urls/timestamps)."""

    claim: str
    confidence: float = Field(ge=0.0, le=1.0)


class _ExtractionOutput(BaseModel):
    """Structured output of an extraction call.

    The model authors ``claim`` + ``confidence`` only; every provenance field is
    code-attached from the `Chunk`/`Source`, never the model.
    """

    claims: list[_ExtractedClaim] = Field(default_factory=list)


class EvidenceExtractionAgent:
    """Extracts `Evidence` from `Chunk`s via the ``EXTRACTION``-role model."""

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def extract(self, chunks: list[Chunk], sources: list[Source]) -> list[Evidence]:
        """Extract grounded `Evidence` from each chunk (per-chunk isolation).

        Resolves each ``chunk.source_id`` against the source registry to fill the
        inline provenance snapshot. Tolerates per-chunk model failures (skip +
        log). Raises `ExtractionError` if a chunk references an unknown source,
        or if no evidence is produced from any chunk (never advance on empty).
        """
        registry = {source.id: source for source in sources}
        model = self._router.for_role(ModelRole.EXTRACTION)
        extracted_via = f"extraction:{model.model}"

        evidence: list[Evidence] = []
        for chunk in chunks:
            source = registry.get(chunk.source_id)
            if source is None:
                raise ExtractionError(
                    f"chunk {chunk.id} references unknown source {chunk.source_id!r}"
                )
            try:
                output = await model.complete_structured(
                    system=SYSTEM_PROMPT,
                    prompt=self._build_prompt(chunk),
                    schema=_ExtractionOutput,
                )
            except Exception as exc:  # one bad chunk must not fail the whole band
                logger.warning("extraction: skipping chunk %s: %s", chunk.id, exc)
                continue
            evidence.extend(
                Evidence(
                    claim=claim.claim,
                    source_id=source.id,
                    source_url=source.url,
                    chunk_id=chunk.id,
                    chunk_text=chunk.text,
                    confidence=claim.confidence,
                    extracted_via=extracted_via,
                )
                for claim in output.claims
            )

        if not evidence:
            raise ExtractionError("extraction produced no evidence from any chunk")
        return evidence

    @staticmethod
    def _build_prompt(chunk: Chunk) -> str:
        return f"Chunk text:\n\n{chunk.text}\n\nExtract the supported claims."
