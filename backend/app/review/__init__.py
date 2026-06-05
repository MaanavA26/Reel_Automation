"""Human-review / approval gate — the deterministic human-sign-off checkpoint.

Per CLAUDE.md §4 this package holds a *tool/service* (no LLM, no judgment in
code): a checkpoint that sits between the automated `safety/gate.py` and publish.
A produced item enters in `PENDING_REVIEW`; a human lists pending items and then
**approves** or **rejects** (with a reason). Only an approved item proceeds.

This is **distinct from `safety/`**: the safety gate is an *automated*,
recomputable, id-/timestamp-free policy verdict; this gate records a *human*
decision as a stateful, id'd, timestamped event. See ADR 0051.
"""

from app.review.base import ReviewStoreBackend
from app.review.errors import ReviewError, ReviewNotFoundError, ReviewTransitionError
from app.review.record import ReviewRecord, ReviewStatus
from app.review.service import ReviewService

__all__ = [
    "ReviewError",
    "ReviewNotFoundError",
    "ReviewRecord",
    "ReviewService",
    "ReviewStatus",
    "ReviewStoreBackend",
    "ReviewTransitionError",
]
