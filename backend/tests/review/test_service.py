"""Hermetic tests for the in-memory `ReviewService` (the human-review gate, ADR 0051).

`ReviewService` is process-local and non-durable by design (mirroring `JobStore`,
ADR 0031), so these tests cover the lifecycle that *is* its contract: submit →
pending, the legal PENDING→APPROVED/REJECTED transitions (with code-attached
provenance), the illegal-transition guard (a second decision raises
`ReviewTransitionError`), unknown-id handling, the reject-requires-reason rule,
and the status-filtered listing. Async methods are driven with `asyncio.run` (the
repo's convention for this offline suite — no `pytest-asyncio` dependency).
"""

from __future__ import annotations

import asyncio

import pytest

from app.review import (
    ReviewNotFoundError,
    ReviewService,
    ReviewStatus,
    ReviewStoreBackend,
    ReviewTransitionError,
)


def test_service_satisfies_the_store_backend_protocol() -> None:
    # The in-memory service is the first concrete backend of the injectable seam;
    # a durable backend (ADR 0040 pattern) would satisfy the same Protocol.
    assert isinstance(ReviewService(), ReviewStoreBackend)


def test_submit_returns_pending_record() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_abc", subject_label="Why fusion matters"))

    assert record.subject_id == "reel_abc"
    assert record.subject_label == "Why fusion matters"
    assert record.status is ReviewStatus.PENDING_REVIEW
    assert record.id.startswith("rev_")
    assert record.decided_at is None
    assert record.decided_via is None
    assert record.reason is None


def test_submit_then_get_round_trips_the_record() -> None:
    service = ReviewService()
    submitted = asyncio.run(service.submit("reel_x"))

    fetched = asyncio.run(service.get(submitted.id))

    assert fetched == submitted


def test_get_unknown_id_returns_none() -> None:
    service = ReviewService()
    assert asyncio.run(service.get("rev_missing")) is None


def test_approve_transitions_to_approved_with_provenance() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_x"))

    approved = asyncio.run(service.approve(record.id, reason="looks good"))

    assert approved.status is ReviewStatus.APPROVED
    assert approved.reason == "looks good"
    assert approved.decided_at is not None
    assert approved.decided_via == "review:service"
    # The decided record is what `get` now returns.
    assert asyncio.run(service.get(record.id)) == approved


def test_approve_without_reason_is_allowed() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_x"))

    approved = asyncio.run(service.approve(record.id))

    assert approved.status is ReviewStatus.APPROVED
    assert approved.reason is None


def test_reject_transitions_to_rejected_with_reason() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_x"))

    rejected = asyncio.run(service.reject(record.id, reason="off-brand"))

    assert rejected.status is ReviewStatus.REJECTED
    assert rejected.reason == "off-brand"
    assert rejected.decided_at is not None


def test_reject_requires_non_blank_reason() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_x"))

    with pytest.raises(ValueError, match="non-blank reason"):
        asyncio.run(service.reject(record.id, reason="   "))

    # The record stays pending — a failed reject does not transition it.
    assert asyncio.run(service.get(record.id)).status is ReviewStatus.PENDING_REVIEW


def test_decide_on_unknown_id_raises_not_found() -> None:
    service = ReviewService()
    with pytest.raises(ReviewNotFoundError, match="rev_missing"):
        asyncio.run(service.approve("rev_missing"))


def test_double_decision_is_an_illegal_transition() -> None:
    service = ReviewService()
    record = asyncio.run(service.submit("reel_x"))
    asyncio.run(service.approve(record.id))

    # A second decision (of either kind) on a decided record is illegal.
    with pytest.raises(ReviewTransitionError, match="already approved"):
        asyncio.run(service.approve(record.id))
    with pytest.raises(ReviewTransitionError, match="already approved"):
        asyncio.run(service.reject(record.id, reason="changed my mind"))


def test_list_filters_by_status_and_orders_newest_first() -> None:
    service = ReviewService()
    first = asyncio.run(service.submit("reel_1"))
    second = asyncio.run(service.submit("reel_2"))
    asyncio.run(service.approve(first.id))

    pending = asyncio.run(service.list_records(status=ReviewStatus.PENDING_REVIEW))
    assert [r.id for r in pending] == [second.id]

    approved = asyncio.run(service.list_records(status=ReviewStatus.APPROVED))
    assert [r.id for r in approved] == [first.id]

    all_records = asyncio.run(service.list_records())
    # Newest first: `second` was submitted after `first`.
    assert {r.id for r in all_records} == {first.id, second.id}
    assert all_records[0].created_at >= all_records[-1].created_at
