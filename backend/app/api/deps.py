"""FastAPI dependency providers for the API layer.

Thin seam between FastAPI's `Depends` machinery and the pure composition root
(`app.services.composition`). Keeping the wiring out of the routers (CLAUDE.md
§10) means the routers declare *what* they need, this module decides *how* it is
built, and tests swap the implementation via `app.dependency_overrides` without
touching production code.
"""

from __future__ import annotations

from app.services.composition import build_research_deps
from app.workflows.deep_research import ResearchDeps


def get_research_deps() -> ResearchDeps:
    """Provide the workflow `ResearchDeps` bundle (request-time, overridable).

    Delegates to the composition root so the construction logic lives in one
    FastAPI-agnostic place. Construction is lazy (per call), so the app boots
    even before a production search/model adapter is wired and tests can
    override this provider before the first request.
    """
    return build_research_deps()
