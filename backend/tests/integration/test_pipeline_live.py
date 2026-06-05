"""Live LLM smoke tests for Deep Research agents (skipped unless a key is set).

Marked ``@pytest.mark.integration`` at module level, so the default ``pytest`` run
(``-m 'not integration'``) and CI never reach these. They run only via
``python -m pytest -m integration`` and *still* no-op (``pytest.skip``) when no
real LLM is configured in ``.env`` — so they are safe to collect offline.

These complement the planner smoke in ``test_live_llm.py`` by exercising the
**extraction** role: the planner and the extractor route to different models in
the policy (``PLANNING`` vs. ``EXTRACTION``), so a live extraction call is net-new
coverage of the structured-output path the workflow's ``extract`` node depends on.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.evidence_extraction import EvidenceExtractionAgent
from app.core.config import Settings
from app.schemas.research_state import Chunk, Source, SourceType
from app.services.llm.factory import build_router_from_settings
from app.services.llm.router import ModelRouter

pytestmark = pytest.mark.integration


def _live_router_or_skip() -> ModelRouter:
    """Build a router from real settings, or skip when no key/base_url is set."""
    settings = Settings()
    if not settings.api_key.get_secret_value() or not settings.base_url:
        pytest.skip("no live LLM key/base_url configured (set them in .env)")
    return build_router_from_settings(settings)


def test_extractor_against_live_model() -> None:
    # Full real path for the EXTRACTION role: Settings -> router -> live model ->
    # structured-output validation -> grounded Evidence. The chunk text carries a
    # concrete, extractable fact so a working model returns >= 1 claim.
    router = _live_router_or_skip()
    source = Source(
        url="https://example.com/water",
        type=SourceType.WEB,
        discovered_via="test:fixture",
    )
    chunk = Chunk(
        source_id=source.id,
        text=(
            "Water is composed of two hydrogen atoms and one oxygen atom, giving it "
            "the chemical formula H2O. It freezes at 0 degrees Celsius at sea level."
        ),
    )

    evidence = asyncio.run(EvidenceExtractionAgent(router).extract([chunk], [source]))

    assert evidence, "live extractor returned no evidence"
    ev = evidence[0]
    # Provenance is code-attached, so a live run must still resolve to the inputs.
    assert ev.source_id == source.id
    assert ev.chunk_id == chunk.id
    assert ev.claim
    assert ev.extracted_via.startswith("extraction:")
