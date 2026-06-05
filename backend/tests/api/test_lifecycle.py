"""Tests for the app-scoped provider lifecycle (ADR 0044).

Two invariants the fix must hold:

* **Build once.** The live `ResearchDeps` bundle is built lazily on first request
  and cached on ``app.state`` — *not* rebuilt per request (the leak the audit
  flagged). Verified by counting builds across two requests through a patched
  composition root.
* **Close on shutdown.** The lifespan drains ``app.state.aclosables`` (the
  providers' httpx clients) when the ``TestClient`` context exits.

Hermetic: the composition root is monkeypatched with a fake bundle, so no live
provider is constructed and no network is touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.research_state import JobStatus
from app.services.composition import ResearchBundle
from tests.api.test_research import _fake_deps


class _SpyClosable:
    """An `AsyncClosable` that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_research_deps_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0
    closable = _SpyClosable()

    def _fake_build() -> ResearchBundle:
        nonlocal builds
        builds += 1
        return ResearchBundle(deps=_fake_deps(), closables=(closable,))

    # Patch where deps.py looks it up (it imports the symbol into its namespace).
    monkeypatch.setattr("app.api.deps.build_research_deps", _fake_build)

    app = create_app()
    with TestClient(app) as client:
        first = client.post("/api/v1/research", json={"topic": "a"})
        second = client.post("/api/v1/research", json={"topic": "b"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == JobStatus.COMPLETED.value
    # The leak fix: one build across two requests (not one per request).
    assert builds == 1
    # The bundle's closable was registered for shutdown and drained on exit.
    assert closable.closed is True


def test_lifespan_closes_registered_clients_on_shutdown() -> None:
    closable = _SpyClosable()
    app = create_app()
    app.state.aclosables.append(closable)

    with TestClient(app):
        assert closable.closed is False  # still open while serving
    # Context exit triggers the lifespan shutdown.
    assert closable.closed is True
