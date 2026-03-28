"""Health endpoint schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response model for health checks."""

    status: Literal["ok"]
    service: str
