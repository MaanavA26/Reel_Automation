"""Composition root: assemble a `ResearchDeps` bundle from `Settings`.

This is the single place that wires the Deep Research workflow's concrete
collaborators (agents + services) together from configuration, mirroring
`app.services.llm.factory.build_router_from_settings` for the model fabric. The
workflow nodes receive everything pre-built via factory-closure DI (ADR 0004,
0009); this module is where those concrete instances are minted.

Pure wiring: deliberately **no FastAPI import** so the boundary stays clean
(CLAUDE.md §10) — the thin request-time `Depends` provider lives in
`app.api.deps` and just calls into here.

Two collaborators are honest holes at this stage and are surfaced as loud
errors rather than silently stubbed:

* **Search.** No production `SearchProvider` exists yet (only the test
  `FakeSearchProvider`); the live adapter is network-gated (M-LP). Building the
  discovery agent therefore raises a clear `CompositionError` until that adapter
  lands, mirroring `factory._build_provider`'s "no adapter registered" pattern.
* **Model provider.** With the default ``default_provider``,
  `build_router_from_settings` itself raises (no adapter registered) — the same
  loud-at-the-seam behavior, inherited for free.

Both are intentional: shipping a `Fake*` as a production default would leak test
doubles into the running service. Tests bypass this entirely by constructing a
fake-backed `ResearchDeps` and overriding the `Depends` provider.
"""

from __future__ import annotations

from app.agents.creator_packet import CreatorPacketAgent
from app.agents.cross_verification import CrossVerificationAgent
from app.agents.editorial_critic import EditorialCriticAgent
from app.agents.evidence_extraction import EvidenceExtractionAgent
from app.agents.report import ReportAgent
from app.agents.research_planner import ResearchPlannerAgent
from app.agents.source_discovery import SourceDiscoveryAgent
from app.agents.synthesis import SynthesisAgent
from app.core.config import Settings, get_settings
from app.services.ingestion.httpx_fetch import HttpxFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.factory import build_router_from_settings
from app.services.search.base import SearchProvider
from app.workflows.deep_research import ResearchDeps


class CompositionError(RuntimeError):
    """A required collaborator could not be assembled from the current settings.

    Raised at composition time (not import time) so the app can boot and tests
    can override the dependency before the first request reaches the workflow.
    """


def _build_search_provider(settings: Settings) -> SearchProvider:
    """Build the production `SearchProvider`, or fail loud if none is wired yet.

    The live search adapter is network-gated and deferred to M-LP. Until it
    lands there is no production backend to return, so this raises rather than
    falling back to a test double (which would ship `FakeSearchProvider` into a
    running service). Mirrors `factory._build_provider`'s explicit error.
    """
    raise CompositionError(
        "no production SearchProvider is wired yet (live search adapter is "
        "network-gated, deferred to M-LP). Override the research-deps dependency "
        "with a fake-backed bundle for tests, or wire a concrete adapter here."
    )


def build_research_deps(settings: Settings | None = None) -> ResearchDeps:
    """Assemble the workflow's `ResearchDeps` bundle from settings.

    All LLM-backed agents share one `ModelRouter` (built from the configured
    provider + role policy); the discovery agent additionally needs a
    `SearchProvider` and ingestion needs a `FetchProvider`. Concrete network
    holes (search, and the default model provider) surface as loud errors at
    call time — see the module docstring.
    """
    resolved = settings or get_settings()
    try:
        router = build_router_from_settings(resolved)
    except ValueError as exc:
        # The model fabric has its own hole (no adapter for the default
        # provider). Normalize it to CompositionError so every wiring failure
        # surfaces through one type (and one HTTP status) at the API seam.
        raise CompositionError(str(exc)) from exc
    search = _build_search_provider(resolved)
    return ResearchDeps(
        planner=ResearchPlannerAgent(router),
        discovery=SourceDiscoveryAgent(router, search),
        ingestion=IngestionService(HttpxFetchProvider()),
        extractor=EvidenceExtractionAgent(router),
        verifier=CrossVerificationAgent(router),
        synthesizer=SynthesisAgent(router),
        critic=EditorialCriticAgent(router),
        reporter=ReportAgent(router),
        strategist=CreatorPacketAgent(router),
    )
