"""Typed Python client for driving the Reel Automation API programmatically.

Re-exports the public surface so callers can ``from app.client import
ReelAutomationClient`` without reaching into the implementation module.
"""

from __future__ import annotations

from app.client.client import (
    API_V1_PREFIX,
    DEFAULT_TIMEOUT_SECONDS,
    ReelAutomationAPIError,
    ReelAutomationClient,
    ReelAutomationError,
)

__all__ = [
    "API_V1_PREFIX",
    "DEFAULT_TIMEOUT_SECONDS",
    "ReelAutomationAPIError",
    "ReelAutomationClient",
    "ReelAutomationError",
]
