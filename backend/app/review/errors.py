"""Typed errors for the human-review gate (ADR 0051).

Kept transport-agnostic: the `ReviewService` raises these, and the API layer maps
them to HTTP status codes (`ReviewNotFoundError` → 404, `ReviewTransitionError` →
409 Conflict). The service never imports FastAPI, mirroring how `JobStore`
returns ``None`` and lets the router own the 404.
"""

from __future__ import annotations


class ReviewError(Exception):
    """Base class for review-gate errors."""


class ReviewNotFoundError(ReviewError):
    """Raised when a decision references an unknown review id."""


class ReviewTransitionError(ReviewError):
    """Raised when a decision is attempted on an already-decided record.

    Approving or rejecting a record that is no longer ``PENDING_REVIEW`` is an
    illegal transition — a human sign-off is recorded once and is not silently
    overwritten. The API maps this to ``409 Conflict``.
    """
