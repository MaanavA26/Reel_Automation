"""FastAPI dependency providers for the API layer.

Thin seam between FastAPI's `Depends` machinery and the pure composition root
(`app.services.composition`). Keeping the wiring out of the routers (CLAUDE.md
§10) means the routers declare *what* they need, this module decides *how* it is
built, and tests swap the implementation via `app.dependency_overrides` without
touching production code.
"""

from __future__ import annotations

from fastapi import Request

from app.review import ReviewService
from app.services.composition import build_research_deps
from app.services.jobs import JobStore
from app.services.video import VideoJobStore, VideoPipeline, build_video_pipeline
from app.workflows.deep_research import ResearchDeps


def get_research_deps(request: Request) -> ResearchDeps:
    """Provide the app-scoped `ResearchDeps` bundle (built once, overridable).

    The bundle and its httpx-owning providers are built **once per app** and
    cached on ``request.app.state.research_deps`` — building per request leaked a
    fresh ``httpx.AsyncClient`` per provider on every call (ADR 0044). The build
    is **lazy** (on first use here, not in `create_app`): the composition root
    raises `CompositionError` when no production model/search backend is wired, so
    eager construction would crash startup and break the "boot then 503 per
    request" contract. On the first successful build, the providers' closables are
    appended to ``app.state.aclosables`` so the lifespan can close them on
    shutdown. Tests override this provider via `app.dependency_overrides` before
    the first request, so the composition root is never reached under test.
    """
    cached: ResearchDeps | None = getattr(request.app.state, "research_deps", None)
    if cached is not None:
        return cached
    bundle = build_research_deps()
    request.app.state.research_deps = bundle.deps
    request.app.state.aclosables.extend(bundle.closables)
    return bundle.deps


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


def get_video_pipeline(request: Request) -> VideoPipeline:
    """Provide the app-scoped end-to-end `VideoPipeline` (built once, overridable).

    Mirrors `get_research_deps`: the pipeline and its httpx-owning providers are
    built **once per app** and cached on ``request.app.state.video_pipeline``
    (building per request leaked a client per provider on every call; ADR 0044).
    The build is **lazy** (on first use), so the app boots even before a live
    model/search/TTS backend is configured and an unconfigured backend surfaces as
    a `CompositionError` mapped to 503 at the seam — never a startup crash. The
    providers' closables are appended to ``app.state.aclosables`` for shutdown.
    Tests override this provider with a fake-backed pipeline before the first
    request, so the composition root is never reached under test.
    """
    cached: VideoPipeline | None = getattr(request.app.state, "video_pipeline", None)
    if cached is not None:
        return cached
    bundle = build_video_pipeline()
    request.app.state.video_pipeline = bundle.pipeline
    request.app.state.aclosables.extend(bundle.closables)
    return bundle.pipeline


def get_video_job_store(request: Request) -> VideoJobStore:
    """Provide the process-singleton `VideoJobStore` held on ``app.state``.

    The video-band analogue of `get_job_store`: stateful, one instance per
    process (enqueue and read must hit the same dict), created once in
    `app.main.create_app` and read off ``request.app.state`` here.
    """
    store: VideoJobStore = request.app.state.video_job_store
    return store


def get_review_service(request: Request) -> ReviewService:
    """Provide the process-singleton `ReviewService` held on ``app.state``.

    The human-review gate analogue of `get_job_store` (ADR 0051): stateful, one
    instance per process (submit, list, and decide must hit the same dict),
    created once in `app.main.create_app` and read off ``request.app.state``
    here. Tests override this provider with a pre-seeded service via
    `app.dependency_overrides`.
    """
    service: ReviewService = request.app.state.review_service
    return service
