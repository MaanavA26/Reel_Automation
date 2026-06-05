"""Typed Python client for the Reel Automation Deep Research API (httpx-based).

A small, synchronous client that lets the Deep Research engine be driven
programmatically (scripts, notebooks, downstream services) without hand-rolling
HTTP. It is a deterministic *tool* (CLAUDE.md §4): it wraps the real API
contract, validates/serializes requests, and parses responses — no reasoning.

Design (mirrors the repo's other httpx adapters — `StockVisualProvider`,
ADR 0021/0024):

- **`base_url` at construction**, a **bounded but generous timeout** (the sync
  `submit_research` endpoint runs the *entire* workflow server-side, so a short
  default would spuriously time out), and an **injectable transport** so the
  whole surface is exercisable offline via `httpx.MockTransport`.
- **Stays in sync with the server by importing the real models** — requests are
  built/validated through `ResearchJobRequest` and responses parsed into the
  canonical `ResearchState` / `HealthResponse`. If those shapes change, the
  client follows for free.
- **Clear, typed errors:** any non-2xx is raised as `ReelAutomationAPIError`
  carrying the HTTP status and the server's parsed ``detail``, instead of
  leaking a raw `httpx.HTTPStatusError`.

Synchronous and single-client by design (CLAUDE.md §7 — no speculative async
surface). Usable as a context manager so the underlying connection pool is
closed deterministically.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from app.api.research import ResearchJobRequest
from app.schemas.health import HealthResponse
from app.schemas.research_state import ResearchState

# The server mounts its routers under this prefix (`Settings.api_v1_prefix`).
# Held as a client-local constant rather than importing `settings`: the client
# should not depend on the server's environment-config machinery for one string.
API_V1_PREFIX = "/api/v1"

# Generous default: the synchronous `POST /research` endpoint runs the full
# Deep Research workflow before responding. Still bounded (never `None`), and
# overridable at construction for slower backends / faster polling loops.
DEFAULT_TIMEOUT_SECONDS = 300.0


class ReelAutomationError(RuntimeError):
    """Base class for all errors raised by `ReelAutomationClient`."""


class ReelAutomationAPIError(ReelAutomationError):
    """Raised when the API returns a non-2xx response.

    Carries the HTTP ``status_code`` and the server-provided ``detail`` (parsed
    from the JSON error body when present, e.g. FastAPI's ``{"detail": ...}``),
    so callers can branch on the status or surface a legible message without
    re-parsing the raw response.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


class ReelAutomationClient:
    """A synchronous, typed client for the Reel Automation API.

    Wraps the four real endpoints (CLAUDE.md §5 Deep Research surface):

    - `health` → ``GET /api/v1/health``
    - `submit_research` → ``POST /api/v1/research`` (synchronous; runs the full
      workflow and returns the terminal `ResearchState`)
    - `enqueue_job` → ``POST /api/v1/research/jobs`` (async; returns the queued
      job id to poll)
    - `get_job` → ``GET /api/v1/research/jobs/{id}`` (status / result snapshot)
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client pointed at ``base_url`` (e.g. ``http://localhost:8000``).

        ``transport`` is injectable so tests can drive the client against an
        `httpx.MockTransport` with no live server; in production it is left as
        ``None`` and httpx uses its default networking transport.
        """
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> ReelAutomationClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- endpoints ---------------------------------------------------------

    def health(self) -> HealthResponse:
        """Return the service health status (``GET /api/v1/health``)."""
        response = self._client.get(f"{API_V1_PREFIX}/health")
        return HealthResponse.model_validate(self._json(response))

    def submit_research(self, topic: str, *, max_syntheses: int | None = None) -> ResearchState:
        """Run a research job to completion and return its terminal `ResearchState`.

        Synchronous: the request blocks server-side for the full workflow
        duration (hence the generous default timeout), and the returned state's
        ``status`` / band substates are the result. ``max_syntheses`` is omitted
        from the payload when ``None`` so the server's own default applies.
        """
        response = self._client.post(
            f"{API_V1_PREFIX}/research",
            json=self._build_request_body(topic, max_syntheses),
        )
        return ResearchState.model_validate(self._json(response))

    def enqueue_job(self, topic: str, *, max_syntheses: int | None = None) -> str:
        """Enqueue a research job and return its id to poll (``POST .../jobs``).

        The async counterpart to `submit_research`: the server returns
        immediately (202) with the ``QUEUED`` state; this method hands back just
        the job ``id`` to pass to `get_job`.
        """
        response = self._client.post(
            f"{API_V1_PREFIX}/research/jobs",
            json=self._build_request_body(topic, max_syntheses),
        )
        return ResearchState.model_validate(self._json(response)).id

    def get_job(self, job_id: str) -> ResearchState:
        """Return a job's current `ResearchState` snapshot by id.

        Raises `ReelAutomationAPIError` (status 404) if the id is unknown — the
        server's 404 ``detail`` is surfaced verbatim.
        """
        response = self._client.get(f"{API_V1_PREFIX}/research/jobs/{job_id}")
        return ResearchState.model_validate(self._json(response))

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _build_request_body(topic: str, max_syntheses: int | None) -> dict[str, Any]:
        """Validate + serialize the request via the server's own request model.

        Building through `ResearchJobRequest` keeps the client in lockstep with
        the API contract (a field change there flows here) and gives free
        client-side validation. ``max_syntheses=None`` is dropped from the wire
        payload so the server applies its own default.
        """
        if max_syntheses is None:
            return ResearchJobRequest(topic=topic).model_dump(exclude={"max_syntheses"})
        return ResearchJobRequest(topic=topic, max_syntheses=max_syntheses).model_dump()

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        """Return the parsed JSON body, raising `ReelAutomationAPIError` on non-2xx.

        Translates a non-2xx response into the typed error carrying the parsed
        ``detail`` (FastAPI's ``{"detail": ...}`` when present, else the raw
        body text), so callers never see a bare `httpx.HTTPStatusError`.
        """
        if response.is_success:
            return response.json()
        raise ReelAutomationAPIError(response.status_code, _extract_detail(response))


def _extract_detail(response: httpx.Response) -> str:
    """Pull a human-readable error detail from a non-2xx response.

    Prefers FastAPI's ``{"detail": ...}`` field; falls back to the raw response
    text when the body is not the expected JSON shape.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return response.text
