"""Health endpoint definitions."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Health check")
def get_health() -> HealthResponse:
    """Return the service health status."""
    return HealthResponse(status="ok", service=settings.app_name)
