"""Shared helpers for the client examples.

Illustrative only (not run in CI). Resolves the API base URL from the
``REEL_AUTOMATION_BASE_URL`` environment variable, defaulting to the local
uvicorn address.
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "http://localhost:8000"


def resolve_base_url() -> str:
    """Return the API base URL (env override, else the local default)."""
    return os.environ.get("REEL_AUTOMATION_BASE_URL", DEFAULT_BASE_URL)
