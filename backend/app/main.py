"""FastAPI application bootstrap."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.research import composition_error_handler
from app.api.router import api_router
from app.core.config import settings
from app.core.lifecycle import AsyncClosable
from app.review import ReviewService
from app.services.composition import CompositionError
from app.services.jobs import JobStore
from app.services.video import VideoJobStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own the app's long-lived resources: nothing to open, drain on shutdown.

    Startup is intentionally empty — the live providers are built **lazily** on
    first request (see `app.api.deps.get_research_deps`), because the composition
    root raises `CompositionError` when unconfigured and eager construction would
    crash an otherwise-bootable app (ADR 0044). On shutdown we close every
    `httpx.AsyncClient` the lazily-built providers registered on
    ``app.state.aclosables`` (otherwise each restart leaks their sockets/FDs), and
    close the job stores' backends if they hold one (the in-memory default does
    not). Close failures are logged, never raised, so one bad client cannot block
    the rest of shutdown.
    """
    yield
    for closable in application.state.aclosables:
        await _safe_aclose(closable)
    _close_store(application.state.job_store)
    _close_store(application.state.video_job_store)


async def _safe_aclose(closable: AsyncClosable) -> None:
    """Close one provider's client, logging (not raising) on failure."""
    try:
        await closable.aclose()
    except Exception:
        # Shutdown must not be blocked by one bad client; log and move on.
        logger.warning("failed to close %r during shutdown", closable, exc_info=True)


def _close_store(store: object) -> None:
    """Close a job-store backend that exposes ``close()`` (e.g. the SQLite store).

    The in-memory `JobStore`/`VideoJobStore` have nothing to close; only the
    `SqliteJobStore` backend holds a connection. Guarded by ``callable`` so the
    default in-memory wiring is a no-op, and failures are logged, not raised.
    """
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Shutdown must not be blocked; log and move on.
            logger.warning("failed to close job store %r during shutdown", store, exc_info=True)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="Reel Automation API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    # Wiring/config failures (no production adapter yet) -> 503, not 500.
    application.add_exception_handler(CompositionError, composition_error_handler)
    # Async-job surface: one process-singleton store, held on app.state so the
    # POST /research/jobs and GET /research/jobs/{id} handlers share state and a
    # restart is the (documented, in-memory) reset boundary. ADR 0031.
    application.state.job_store = JobStore()
    # The video-band async surface gets its own process-singleton store (ADR 0032),
    # symmetric with the research job store above.
    application.state.video_job_store = VideoJobStore()
    # The human-review / approval gate's store (ADR 0051): one process-singleton
    # so submit/list/decide share state, symmetric with the job stores above. The
    # in-memory store has nothing to close, so it is not added to the shutdown path.
    application.state.review_service = ReviewService()
    # App-scoped, lazily-built provider bundles (ADR 0044). The deps/pipeline are
    # built once on first request (the composition root can raise when unwired, so
    # they are not built eagerly here); each build appends its httpx-owning
    # providers to ``aclosables`` for the lifespan to close on shutdown. Seeded
    # here so the slots exist before the first request.
    application.state.research_deps = None
    application.state.video_pipeline = None
    aclosables: list[AsyncClosable] = []
    application.state.aclosables = aclosables
    return application


app = create_app()
