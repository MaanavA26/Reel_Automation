"""FastAPI application bootstrap."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.research import composition_error_handler
from app.api.router import api_router
from app.core.config import settings
from app.services.composition import CompositionError
from app.services.jobs import JobStore


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="Reel Automation API",
        version="0.1.0",
    )
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    # Wiring/config failures (no production adapter yet) -> 503, not 500.
    application.add_exception_handler(CompositionError, composition_error_handler)
    # Async-job surface: one process-singleton store, held on app.state so the
    # POST /research/jobs and GET /research/jobs/{id} handlers share state and a
    # restart is the (documented, in-memory) reset boundary. ADR 0031.
    application.state.job_store = JobStore()
    return application


app = create_app()
