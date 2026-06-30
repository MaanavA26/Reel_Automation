"""Per-stage structured event helper, layered on the app's JSON logger.

A deterministic *tool* (CLAUDE.md §4): a thin convenience over the structured
logging already configured by :mod:`app.core.logging` (ADR 0030). It does **not**
reinvent the JSON formatter or the run-id correlation — the formatter still stamps
``ts`` (ISO-8601 UTC) and the active ``run_id`` onto every line. This helper only
attaches a small, *metadata-only* payload (``stage`` + numeric counts/durations)
to a log record so a stage transition is queryable in shipped logs.

It relies on the single additive ``event`` seam in :class:`app.core.logging.JsonFormatter`
(ADR 0057): the helper attaches an ``event`` mapping via the record ``extra`` and
the formatter emits it under an ``event`` key. A normal log line (no ``event``
attribute) is byte-identical to before, so this never perturbs existing logs.

Info-leak discipline (ADR 0043)
-------------------------------
The records sink persists the research *bodies* (it writes to a gitignored dir);
the app logger must not. So `log_stage_event` accepts only a stage name and
**numeric** metrics (counts, durations) — never claim text, narration, urls, or
``state.error`` strings. The signature enforces this structurally: ``metrics`` is
typed ``Mapping[str, int | float]``, so a body string cannot be passed without a
type error. This is the exact leak vector ADR 0043 closed, kept closed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def log_stage_event(
    stage: str,
    *,
    metrics: Mapping[str, int | float] | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit a structured per-stage event correlated by the active ``run_id``.

    Args:
        stage: A short, stable stage identifier (e.g. ``"plan"``, ``"acquire"``,
            ``"synthesize"``, ``"render"``). Keep it a controlled vocabulary so
            shipped logs are groupable.
        metrics: Optional numeric metadata for the stage — counts, durations in
            milliseconds, token/cost tallies. **Numbers only**: no bodies, urls,
            or error strings (ADR 0043 leak guard, enforced by the type).
        level: Logging level for the event (defaults to ``INFO``).

    The active ``run_id`` and the UTC ``ts`` are supplied automatically by the
    structured formatter (ADR 0030) — this helper never sets them, so it stays a
    pure pass-through to the configured logger.
    """
    event: dict[str, object] = {"stage": stage, "metrics": dict(metrics or {})}
    logger.log(level, "stage:%s", stage, extra={"event": event})
