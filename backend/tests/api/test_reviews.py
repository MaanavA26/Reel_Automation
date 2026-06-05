"""Tests for the human-review / approval gate API surface (ADR 0051).

Hermetic and network-free: the router is driven through FastAPI's `TestClient`
against the real in-memory `ReviewService` held on ``app.state`` (no LLM, no I/O,
nothing to fake). Because ADR 0051 ships *no* create route (the producer-side
`submit` is capability-before-wiring), the fixture seeds the store directly via
``app.state.review_service`` before exercising the list/approve/reject routes —
exactly how a producer would enqueue an item for review.

Covers the HTTP contract: the pending list + status filter, the approve/reject
transitions, the 404 on an unknown id, the 409 on an illegal (double) decision,
and the 422 on a reject with a blank reason.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.review import ReviewRecord, ReviewService


@pytest.fixture
def app_and_client() -> Iterator[tuple[TestClient, ReviewService]]:
    """A TestClient plus the app's real in-memory `ReviewService` for seeding."""
    app = create_app()
    service: ReviewService = app.state.review_service
    with TestClient(app) as client:
        yield client, service


def _seed(service: ReviewService, subject_id: str = "reel_x", label: str = "L") -> ReviewRecord:
    """Submit a pending record directly on the store (no create route exists)."""
    return asyncio.run(service.submit(subject_id, subject_label=label))


# --- GET /reviews (list + status filter) -------------------------------------


def test_list_reviews_returns_seeded_records(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, service = app_and_client
    record = _seed(service, "reel_a", "Hook A")

    response = client.get("/api/v1/reviews")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == record.id
    assert body[0]["status"] == "pending_review"
    assert body[0]["subject_label"] == "Hook A"


def test_list_reviews_filters_by_status(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, service = app_and_client
    pending = _seed(service, "reel_p")
    decided = _seed(service, "reel_d")
    asyncio.run(service.approve(decided.id))

    pending_only = client.get("/api/v1/reviews", params={"status": "pending_review"}).json()
    assert [r["id"] for r in pending_only] == [pending.id]

    approved_only = client.get("/api/v1/reviews", params={"status": "approved"}).json()
    assert [r["id"] for r in approved_only] == [decided.id]


def test_list_reviews_rejects_unknown_status(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, _ = app_and_client
    assert client.get("/api/v1/reviews", params={"status": "bogus"}).status_code == 422


# --- POST /reviews/{id}/approve ----------------------------------------------


def test_approve_transitions_record(app_and_client: tuple[TestClient, ReviewService]) -> None:
    client, service = app_and_client
    record = _seed(service)

    response = client.post(f"/api/v1/reviews/{record.id}/approve", json={"reason": "ship it"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["reason"] == "ship it"
    assert body["decided_via"] == "review:api"
    assert body["decided_at"] is not None


def test_approve_without_reason_is_allowed(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, service = app_and_client
    record = _seed(service)

    response = client.post(f"/api/v1/reviews/{record.id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_approve_unknown_id_returns_404(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, _ = app_and_client
    response = client.post("/api/v1/reviews/rev_missing/approve", json={})

    assert response.status_code == 404
    assert "rev_missing" in response.json()["detail"]


def test_double_decision_returns_409(app_and_client: tuple[TestClient, ReviewService]) -> None:
    client, service = app_and_client
    record = _seed(service)
    client.post(f"/api/v1/reviews/{record.id}/approve", json={})

    response = client.post(f"/api/v1/reviews/{record.id}/approve", json={})

    assert response.status_code == 409
    assert "already approved" in response.json()["detail"]


# --- POST /reviews/{id}/reject -----------------------------------------------


def test_reject_transitions_record(app_and_client: tuple[TestClient, ReviewService]) -> None:
    client, service = app_and_client
    record = _seed(service)

    response = client.post(f"/api/v1/reviews/{record.id}/reject", json={"reason": "off-brand"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "off-brand"


def test_reject_requires_reason(app_and_client: tuple[TestClient, ReviewService]) -> None:
    client, service = app_and_client
    record = _seed(service)

    # Missing reason -> 422 (request validation); blank reason -> 422 (min_length).
    assert client.post(f"/api/v1/reviews/{record.id}/reject", json={}).status_code == 422
    assert (
        client.post(f"/api/v1/reviews/{record.id}/reject", json={"reason": ""}).status_code == 422
    )


def test_reject_unknown_id_returns_404(
    app_and_client: tuple[TestClient, ReviewService],
) -> None:
    client, _ = app_and_client
    response = client.post("/api/v1/reviews/rev_missing/reject", json={"reason": "no"})

    assert response.status_code == 404
