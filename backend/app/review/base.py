"""The `ReviewStoreBackend` protocol — the injectable seam review stores satisfy.

Mirrors `app.services.jobs.base.JobStoreBackend`: a small typed `Protocol` that
lets a backend-agnostic caller annotate the seam without coupling to the concrete
class. ADR 0051 ships a single in-memory `ReviewService`; a durable backend (e.g.
the SQLite pattern of ADR 0040) is deferred and would satisfy this same contract.

The protocol covers only the four operations the router/producer use
(`submit`/`get`/`list_records`/`approve`/`reject`). ``@runtime_checkable`` so a
test can assert conformance via ``isinstance`` (method presence only — protocols
cannot runtime-check signatures).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.review.record import ReviewRecord, ReviewStatus


@runtime_checkable
class ReviewStoreBackend(Protocol):
    """Structural interface for a human-review record store."""

    async def submit(self, subject_id: str, *, subject_label: str = "") -> ReviewRecord:
        """Register a new ``PENDING_REVIEW`` record for ``subject_id``."""
        ...

    async def get(self, review_id: str) -> ReviewRecord | None:
        """Return the current record for ``review_id``, or ``None`` if unknown."""
        ...

    async def list_records(self, *, status: ReviewStatus | None = None) -> list[ReviewRecord]:
        """Return records, optionally filtered to a single ``status``."""
        ...

    async def approve(
        self, review_id: str, *, reason: str | None = None, decided_via: str = "review:service"
    ) -> ReviewRecord:
        """Transition a pending record to ``APPROVED`` and return it."""
        ...

    async def reject(
        self, review_id: str, *, reason: str, decided_via: str = "review:service"
    ) -> ReviewRecord:
        """Transition a pending record to ``REJECTED`` (reason required) and return it."""
        ...
