"""Structured logging setup for the backend.

A deterministic *tool* (CLAUDE.md §4 — no judgment, no LLM) that configures
stdlib :mod:`logging` so every line is a single JSON object. It correlates a
Deep Research job's logs by reading the active ``run_id`` from
:mod:`app.core.run_context` at format time — no logging-call site needs to know
about the run id, and existing ``logging.getLogger(__name__)`` callers across the
codebase gain correlation for free once :func:`setup_logging` has run.

Pure stdlib (CLAUDE.md §10 — no new dependency). The app entrypoint is expected
to call :func:`setup_logging` once at startup; this module deliberately does not
configure logging on import (importing a library should never reconfigure the
root logger).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.run_context import get_run_id


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one single-line JSON object.

    Emitted keys: ``ts`` (ISO-8601 UTC), ``level``, ``logger``, ``message``, and
    ``run_id`` (the active :mod:`app.core.run_context` value, or ``null``). When
    the record carries exception info, a ``exc_info`` string is appended. Output
    is single-line by construction: :func:`json.dumps` escapes embedded newlines,
    preserving "one log line = one JSON object".
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object | None] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": get_run_id(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # ``default=str`` keeps the formatter total: a stray non-serializable
        # value in a message degrades to its string form instead of raising
        # inside the logging machinery.
        return json.dumps(payload, default=str)


def setup_logging(level: int | str = logging.INFO, *, json: bool = True) -> None:
    """Configure the root logger for the process.

    Args:
        level: Minimum level for the root logger (``int`` or level name).
        json: When ``True`` (default) emit structured JSON via
            :class:`JsonFormatter`; when ``False`` fall back to a plain
            human-readable text formatter (still carries the ``run_id``).

    Idempotent: existing handlers on the root logger are removed first, so
    repeated calls (e.g. tests, reloads) do not duplicate log output. All logs
    are written to ``stdout`` for predictable container/log-shipper capture.
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter
    if json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [run_id=%(run_id)s] %(message)s"
        )
        # The plain formatter references ``run_id`` via a record attribute, so
        # inject it for every record regardless of the logging call site.
        handler.addFilter(_RunIdFilter())
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


class _RunIdFilter(logging.Filter):
    """Attach the active ``run_id`` to each record (for the plain-text formatter).

    The JSON formatter reads the context var directly, but a printf-style
    ``logging.Formatter`` can only reference record attributes, so the value has
    to be stamped onto the record before formatting.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        return True
