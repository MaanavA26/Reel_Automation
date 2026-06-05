"""Async job orchestration band — the in-memory `JobStore` service.

Per CLAUDE.md §4 this is a *service/tool*, not an agent: it owns deterministic
job-lifecycle bookkeeping (enqueue → run → record terminal state) and addressable
status reads, while the reasoning lives entirely in the workflow it invokes. The
Research Control band's "job lifecycle / progress tracking" responsibility
(CLAUDE.md §5.5 A) gets its first concrete, HTTP-reachable home here.

The default member is `JobStore` (see `store`): process-local and non-durable by
design — see its class docstring and ADR 0031 for the single-process limitation.
`SqliteJobStore` (see `sqlite_store`, ADR 0040) is the durable backend that survives
process restarts; both satisfy the `JobStoreBackend` protocol (see `base`), the
injectable seam that lets callers stay backend-agnostic.
"""

from app.services.jobs.base import JobStoreBackend
from app.services.jobs.sqlite_store import SqliteJobStore
from app.services.jobs.store import JobRunner, JobStore

__all__ = ["JobRunner", "JobStore", "JobStoreBackend", "SqliteJobStore"]
