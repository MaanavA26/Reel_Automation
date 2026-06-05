"""Top-level API router configuration."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.research import router as research_router
from app.api.videos import router as videos_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(research_router)
api_router.include_router(videos_router)
