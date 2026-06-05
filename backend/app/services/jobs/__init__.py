"""Async job orchestration band — the in-memory `JobStore` service.

Per CLAUDE.md §4 this is a *service/tool*, not an agent: it owns deterministic
job-lifecycle bookkeeping (enqueue → run → record terminal state) and addressable
status reads, while the reasoning lives entirely in the workflow it invokes. The
Research Control band's "job lifecycle / progress tracking" responsibility
(CLAUDE.md §5.5 A) gets its first concrete, HTTP-reachable home here.

The single member is `JobStore` (see `store`). It is process-local and
non-durable by design — see the class docstring and ADR 0031 for the explicit
single-process limitation and the deferral of a durable, cross-worker store.
"""

from app.services.jobs.store import JobStore

__all__ = ["JobStore"]
