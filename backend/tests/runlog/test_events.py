"""Tests for the per-stage structured-event helper + its formatter seam (ADR 0057).

These assert the structured ``event`` payload actually lands in the JSON line
(not merely that the call does not raise) and — critically — that a normal log
line carrying no ``event`` attribute is byte-identical to before, so the additive
formatter change never perturbs existing logs.
"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter
from app.core.run_context import run_context
from app.services.runlog.events import log_stage_event


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_normal_line_has_no_event_key() -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert "event" not in payload
    assert set(payload) == {"ts", "level", "logger", "message", "run_id"}


def test_event_attribute_serializes_under_event_key() -> None:
    event = {"stage": "synthesize", "metrics": {"findings": 3, "duration_ms": 12}}
    payload = json.loads(JsonFormatter().format(_record(event=event)))
    assert payload["event"] == event


def test_log_stage_event_emits_structured_payload(caplog) -> None:  # type: ignore[no-untyped-def]
    # The formatter reads the active run_id at *format* time (ADR 0030), so format
    # inside the run_context to exercise the correlation path.
    with caplog.at_level(logging.INFO, logger="app.services.runlog.events"):
        with run_context("run_abc"):
            log_stage_event("acquire", metrics={"sources": 2})
            record = next(r for r in caplog.records if getattr(r, "event", None) is not None)
            line = json.loads(JsonFormatter().format(record))

    assert line["event"] == {"stage": "acquire", "metrics": {"sources": 2}}
    assert line["run_id"] == "run_abc"
    assert line["message"] == "stage:acquire"


def test_log_stage_event_defaults_to_empty_metrics(caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.INFO, logger="app.services.runlog.events"):
        log_stage_event("plan")
    record = next(r for r in caplog.records if getattr(r, "event", None) is not None)
    assert record.event == {"stage": "plan", "metrics": {}}
