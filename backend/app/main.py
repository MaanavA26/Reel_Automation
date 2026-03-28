"""FastAPI application bootstrap."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="Reel Automation API",
        version="0.1.0",
    )
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
