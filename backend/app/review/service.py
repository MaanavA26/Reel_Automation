"""In-memory `ReviewService`: the deterministic human-review / approval gate (ADR 0051).

Per CLAUDE.md §4 this is a *tool/service*, not an agent: it owns deterministic
record-keeping for a human sign-off — minting a ``PENDING_REVIEW`` record,
listing pending items, and transitioning a record to ``APPROVED`` / ``REJECTED``
— while the *judgment* (whether to approve, and the reason) is supplied by a
human, not an LLM. It is the checkpoint that sits between the automated
`safety/gate.py` (which produces a recomputable `SafetyVerdict`) and publish.

**The store records an event in time.** Unlike the pure `PrePublishGate`, a
decision here is timestamped and stateful: code attaches the ``decided_at`` /
``decided_via`` provenance (CLAUDE.md §11) and enforces the legal transition
(only a ``PENDING_REVIEW`` record may be decided — a second decision raises
`ReviewTransitionError` rather than silently overwriting the first sign-off).

**Single-process, non-durable by design.** Records live in a plain dict guarded
by one `asyncio.Lock`, the same model `JobStore` (ADR 0031) documents — a restart
resets it, and a durable/cross-worker backend (e.g. the SQLite store of ADR 0040)
is deferred. The store is held as a process-singleton on ``app.state`` (see
`app.main`), never rebuilt per request.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.review.errors import ReviewNotFoundError, ReviewTransitionError
from app.review.record import ReviewRecord, ReviewStatus

logger = logging.getLogger(__name__)

# How a decision records the channel it came through (CLAUDE.md §11 provenance).
# The API passes ``"review:api"``; a CLI or background producer would pass its own.
_DEFAULT_DECIDED_VIA = "review:service"


class ReviewService:
    """Process-local registry of human-review records, keyed by `ReviewRecord.id`.

    Not durable and not cross-worker (see the module docstring / ADR 0031). All
    mutations are serialized by a single `asyncio.Lock`; the store runs on one
    event loop, so the lock is for honesty/future-proofing rather than to tame
    real contention — symmetric with `JobStore`.
    """

    def __init__(self) -> None:
        self._records: dict[str, ReviewRecord] = {}
        self._lock = asyncio.Lock()

    async def submit(self, subject_id: str, *, subject_label: str = "") -> ReviewRecord:
        """Register a new ``PENDING_REVIEW`` record for ``subject_id`` and return it.

        The producer-side entrypoint: a finished item (e.g. a `VideoArtifact`)
        enters the gate here. No HTTP route exposes this in ADR 0051 — wiring the
        producer (pipeline / post-safety-gate handoff) into it is a documented
        follow-up (the "capability before wiring" pattern of ADR 0041); the
        capability exists so the pending list and the decision routes are usable.
        """
        record = ReviewRecord(subject_id=subject_id, subject_label=subject_label)
        async with self._lock:
            self._records[record.id] = record
        logger.info("submitted review %s for subject %s", record.id, subject_id)
        return record

    async def get(self, review_id: str) -> ReviewRecord | None:
        """Return the current record for ``review_id``, or ``None`` if unknown.

        ``None`` is the not-found signal; translating it into an HTTP 404 is the
        router's concern (this service stays transport-agnostic).
        """
        async with self._lock:
            return self._records.get(review_id)

    async def list_records(self, *, status: ReviewStatus | None = None) -> list[ReviewRecord]:
        """Return records (newest first), optionally filtered to one ``status``.

        ``status=None`` returns every record; ``status=PENDING_REVIEW`` is the
        review-queue view. Ordered by ``created_at`` descending so the most recent
        items surface first.
        """
        async with self._lock:
            records = list(self._records.values())
        if status is not None:
            records = [r for r in records if r.status is status]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def approve(
        self, review_id: str, *, reason: str | None = None, decided_via: str = _DEFAULT_DECIDED_VIA
    ) -> ReviewRecord:
        """Transition a pending record to ``APPROVED`` and return the decided record.

        ``reason`` is optional on approve (an approval needs no justification); the
        approved record is what proceeds to publish. ``decided_via`` records the
        channel the decision came through (CLAUDE.md §11 provenance) — the API
        passes ``"review:api"``; the default suits a CLI/test caller.
        """
        return await self._decide(
            review_id,
            status=ReviewStatus.APPROVED,
            reason=reason,
            decided_via=decided_via,
        )

    async def reject(
        self, review_id: str, *, reason: str, decided_via: str = _DEFAULT_DECIDED_VIA
    ) -> ReviewRecord:
        """Transition a pending record to ``REJECTED`` (reason required) and return it.

        A rejection must carry a non-blank human-authored ``reason`` — a rejected
        item does not proceed to publish, and the reason is the audit trail.
        ``decided_via`` records the deciding channel (see `approve`). Raises
        `ValueError` on a blank reason.
        """
        if reason is None or reason.strip() == "":
            raise ValueError("a non-blank reason is required to reject a review")
        return await self._decide(
            review_id,
            status=ReviewStatus.REJECTED,
            reason=reason,
            decided_via=decided_via,
        )

    async def _decide(
        self,
        review_id: str,
        *,
        status: ReviewStatus,
        reason: str | None,
        decided_via: str,
    ) -> ReviewRecord:
        """Apply a terminal decision to a pending record, attaching code-owned provenance.

        Enforces the only legal transition (``PENDING_REVIEW`` → terminal): a
        decision on an unknown id raises `ReviewNotFoundError`; a decision on an
        already-decided record raises `ReviewTransitionError` (no silent
        overwrite). On success, code stamps ``status`` / ``decided_at`` /
        ``decided_via`` while ``reason`` carries the human's authored judgment
        (CLAUDE.md §11).
        """
        async with self._lock:
            current = self._records.get(review_id)
            if current is None:
                raise ReviewNotFoundError(f"no review with id {review_id!r}")
            if current.status.is_terminal:
                raise ReviewTransitionError(
                    f"review {review_id!r} is already {current.status.value}; "
                    "cannot decide an already-decided record"
                )
            decided = current.model_copy(
                update={
                    "status": status,
                    "reason": reason,
                    "decided_at": datetime.now(UTC),
                    "decided_via": decided_via,
                }
            )
            self._records[review_id] = decided
        logger.info("review %s decided: %s", review_id, status.value)
        return decided
