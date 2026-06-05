"""The `JobStoreBackend` protocol — the injectable seam both job stores satisfy.

ADR 0031 shipped a single concrete in-memory `JobStore`. ADR 0040 adds a durable
`SqliteJobStore` alongside it; this protocol is the small typed seam that lets the
two be used interchangeably without coupling callers to either class.

Why a *separate* protocol rather than renaming `JobStore`: the composition root
(`app.main.create_app`) instantiates the in-memory store as ``JobStore()``, and a
`typing.Protocol` cannot be instantiated. So the concrete class keeps its name and
its behavior unchanged (backward-compatible — `deps.py`/`research.py` still annotate
concrete `JobStore` and keep working verbatim), and this protocol is the *structural*
contract both backends already meet. New code that wants to be backend-agnostic can
annotate `JobStoreBackend`; nothing is forced to migrate.

The protocol covers only the three lifecycle methods the router uses
(`enqueue`/`get`/`run`). Backend-specific affordances (e.g. `SqliteJobStore.close`)
are deliberately out of the protocol — they are not part of the shared contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.research_state import ResearchState
from app.services.jobs.store import JobRunner


@runtime_checkable
class JobStoreBackend(Protocol):
    """Structural interface for a Deep Research job store.

    Satisfied by the in-memory `JobStore` (ADR 0031) and the durable
    `SqliteJobStore` (ADR 0040). ``@runtime_checkable`` so a test can assert a
    backend conforms via ``isinstance`` (method presence only — protocols cannot
    runtime-check signatures).
    """

    async def enqueue(self, topic: str) -> ResearchState:
        """Register a new ``QUEUED`` job for ``topic`` and return its snapshot."""
        ...

    async def get(self, job_id: str) -> ResearchState | None:
        """Return the current snapshot for ``job_id``, or ``None`` if unknown."""
        ...

    async def run(self, job_id: str, runner: JobRunner) -> None:
        """Run a queued job to its terminal state via ``runner`` and record it."""
        ...
