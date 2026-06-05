"""Human-review / approval gate API endpoints (ADR 0051).

A deliberately **thin** router (CLAUDE.md §10): it validates the request,
delegates to the `ReviewService` (which owns all decision/transition logic), and
maps the service's typed errors to HTTP status codes. The router owns only the
HTTP contract — listing pending items and recording approve/reject decisions.

Three surfaces:

* ``GET /reviews?status=pending`` lists review records, optionally filtered.
* ``POST /reviews/{id}/approve`` approves a pending record (optional reason).
* ``POST /reviews/{id}/reject`` rejects a pending record (reason required in body).

A decision on an unknown id is a ``404``; a decision on an already-decided record
is a ``409 Conflict`` (an illegal transition — a human sign-off is recorded once,
never silently overwritten).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_review_service
from app.review import (
    ReviewNotFoundError,
    ReviewRecord,
    ReviewService,
    ReviewStatus,
    ReviewTransitionError,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])

# The channel a decision came through, attached as provenance on the record
# (CLAUDE.md §11). Set here (not in the service) so each call site is honest about
# its origin; the API's decisions are all ``review:api``.
_DECIDED_VIA = "review:api"


class ApproveRequest(BaseModel):
    """Payload to approve a pending review (an optional rationale).

    Kept inline in the router (not in `schemas/`) since it is a small, API-local
    contract. ``reason`` is optional on approve — an approval needs no
    justification, though one may be recorded.
    """

    model_config = {"extra": "forbid"}

    reason: str | None = Field(default=None, description="Optional approval rationale.")


class RejectRequest(BaseModel):
    """Payload to reject a pending review (a required, non-blank rationale).

    A rejection must carry a human-authored reason — it is the audit trail for an
    item that does not proceed to publish.
    """

    model_config = {"extra": "forbid"}

    reason: str = Field(
        min_length=1, description="Required human-authored rationale for the rejection."
    )


@router.get(
    "",
    response_model=list[ReviewRecord],
    status_code=status.HTTP_200_OK,
    summary="List review records, optionally filtered by status",
)
async def list_reviews(
    service: Annotated[ReviewService, Depends(get_review_service)],
    status_filter: Annotated[
        ReviewStatus | None,
        # Exposed as ``?status=pending_review`` (the enum value); ``None`` → all.
        # Aliased so the query param is the conventional ``status`` while the
        # Python name avoids shadowing the imported ``status`` module.
        Query(alias="status"),
    ] = None,
) -> list[ReviewRecord]:
    """Return review records (newest first), optionally filtered to one status.

    Omitting ``status`` returns every record; ``?status=pending_review`` is the
    review-queue view a human works through.
    """
    return await service.list_records(status=status_filter)


@router.post(
    "/{review_id}/approve",
    response_model=ReviewRecord,
    status_code=status.HTTP_200_OK,
    summary="Approve a pending review and return the decided record",
)
async def approve_review(
    review_id: str,
    request: ApproveRequest,
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewRecord:
    """Approve the pending record, transitioning it to ``APPROVED``.

    404 if the id is unknown; 409 if the record was already decided.
    """
    try:
        return await service.approve(review_id, reason=request.reason, decided_via=_DECIDED_VIA)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/{review_id}/reject",
    response_model=ReviewRecord,
    status_code=status.HTTP_200_OK,
    summary="Reject a pending review and return the decided record",
)
async def reject_review(
    review_id: str,
    request: RejectRequest,
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewRecord:
    """Reject the pending record, transitioning it to ``REJECTED``.

    404 if the id is unknown; 409 if the record was already decided. A blank
    reason is rejected as a 422 by request validation (``min_length=1``).
    """
    try:
        return await service.reject(review_id, reason=request.reason, decided_via=_DECIDED_VIA)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
