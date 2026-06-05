"""Run-scoped correlation context for Deep Research jobs.

A deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM): a thin wrapper over
a `contextvars.ContextVar` that carries the current job's ``run_id`` through the
call stack (sync or async) without threading it through every signature. The
structured logging formatter (``app.core.logging``) reads it at format time so
every log line a Deep Research run emits is correlatable, regardless of which
module logged it.

`contextvars` is async- and thread-safe by construction: each asyncio task and
each thread sees its own value, so concurrent runs never bleed ``run_id``s into
one another's logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

# Module-private so the only supported access is via the helpers below; this
# keeps the binding lifecycle (set + guaranteed reset) in one place.
_run_id_var: ContextVar[str | None] = ContextVar("reel_automation_run_id", default=None)


def get_run_id() -> str | None:
    """Return the ``run_id`` bound to the current context, or ``None`` if unset."""
    return _run_id_var.get()


def bind_run_id(run_id: str) -> Token[str | None]:
    """Bind ``run_id`` to the current context.

    Returns the ``Token`` needed to restore the previous value. Prefer the
    :func:`run_context` context manager, which resets automatically; reach for
    this lower-level helper only when entry and exit cannot be expressed as a
    single lexical scope.
    """
    return _run_id_var.set(run_id)


def reset_run_id(token: Token[str | None]) -> None:
    """Restore the ``run_id`` to the value captured in ``token`` by :func:`bind_run_id`."""
    _run_id_var.reset(token)


@contextmanager
def run_context(run_id: str) -> Iterator[str]:
    """Bind ``run_id`` for the duration of the ``with`` block, then restore it.

    Reset happens in a ``finally``, so the prior value is restored even if the
    block raises — nested runs and exceptions cannot leak a stale ``run_id``.

    Example::

        with run_context("run_abc123"):
            logger.info("planning started")  # log line carries run_id=run_abc123
    """
    token = bind_run_id(run_id)
    try:
        yield run_id
    finally:
        reset_run_id(token)
