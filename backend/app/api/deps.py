"""FastAPI dependency providers for the API layer.

Thin seam between FastAPI's `Depends` machinery and the pure composition root
(`app.services.composition`). Keeping the wiring out of the routers (CLAUDE.md
§10) means the routers declare *what* they need, this module decides *how* it is
built, and tests swap the implementation via `app.dependency_overrides` without
touching production code.
"""

from __future__ import annotations

from fastapi import Request

from app.services.composition import build_research_deps
from app.services.jobs import JobStore
from app.workflows.deep_research import ResearchDeps


def get_research_deps() -> ResearchDeps:
    """Provide the workflow `ResearchDeps` bundle (request-time, overridable).

    Delegates to the composition root so the construction logic lives in one
    FastAPI-agnostic place. Construction is lazy (per call), so the app boots
    even before a production search/model adapter is wired and tests can
    override this provider before the first request.
    """
    return build_research_deps()


def get_job_store(request: Request) -> JobStore:
    """Provide the process-singleton `JobStore` held on ``app.state``.

    Unlike `get_research_deps`, the store is **stateful** and must be one
    instance per process — enqueue (POST) and read (GET) have to hit the same
    dict or every status read 404s. It is therefore created once in
    `app.main.create_app` and read off ``request.app.state`` here, never rebuilt
    per request. Each `create_app()` gets its own store, which gives tests
    per-app isolation without an override.
    """
    store: JobStore = request.app.state.job_store
    return store
