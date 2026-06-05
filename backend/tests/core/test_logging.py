"""Hermetic tests for the structured logging + run-tracing scaffold.

Two layers, neither mutating global root-logger state:

1. ``JsonFormatter`` formatted directly from a constructed ``LogRecord`` — pure,
   no handlers, no globals.
2. The contextvar binding exercised end-to-end through a logger that writes to an
   in-memory ``StringIO`` — proving a line emitted inside ``run_context`` carries
   the bound ``run_id`` and that the value is restored on exit.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from io import StringIO

import pytest

from app.core.logging import JsonFormatter, setup_logging
from app.core.run_context import (
    bind_run_id,
    get_run_id,
    reset_run_id,
    run_context,
)


@pytest.fixture(autouse=True)
def _isolate_run_context() -> Iterator[None]:
    """Guarantee no test leaks a bound ``run_id`` into another."""
    token = bind_run_id("__sentinel_should_be_overwritten__")
    reset_run_id(token)  # back to whatever the prior state was (default None)
    assert get_run_id() is None
    yield
    assert get_run_id() is None


def _make_record(message: str = "hello", name: str = "test.logger") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_formatter_emits_valid_single_line_json() -> None:
    line = JsonFormatter().format(_make_record())

    assert "\n" not in line  # one log line == one JSON object
    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert "ts" in payload


def test_formatter_run_id_is_null_outside_run_context() -> None:
    payload = json.loads(JsonFormatter().format(_make_record()))

    # Stable schema: the key is always present, ``null`` when unbound.
    assert "run_id" in payload
    assert payload["run_id"] is None


def test_formatter_carries_bound_run_id() -> None:
    with run_context("run_abc123"):
        payload = json.loads(JsonFormatter().format(_make_record()))

    assert payload["run_id"] == "run_abc123"


def test_message_newlines_stay_on_one_line() -> None:
    line = JsonFormatter().format(_make_record(message="line1\nline2"))

    assert line.count("\n") == 0  # embedded newline is JSON-escaped
    assert json.loads(line)["message"] == "line1\nline2"


def test_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record(message="failed")
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_run_context_correlates_logs_emitted_inside_it() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.correlation")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # keep this off the root logger; fully hermetic

    try:
        with run_context("run_xyz789"):
            logger.info("inside the run")
        logger.info("outside the run")
    finally:
        logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["message"] == "inside the run"
    assert lines[0]["run_id"] == "run_xyz789"
    assert lines[1]["message"] == "outside the run"
    assert lines[1]["run_id"] is None


def test_run_context_restores_previous_run_id_after_exit() -> None:
    assert get_run_id() is None
    with run_context("outer"):
        assert get_run_id() == "outer"
        with run_context("inner"):
            assert get_run_id() == "inner"
        assert get_run_id() == "outer"
    assert get_run_id() is None


def test_run_context_resets_even_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with run_context("transient"):
            raise RuntimeError("boom")
    assert get_run_id() is None


def test_setup_logging_is_idempotent_and_emits_json() -> None:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(level=logging.INFO)
        setup_logging(level=logging.INFO)  # second call must not duplicate handlers
        assert len(root.handlers) == 1

        # Redirect the configured handler to an in-memory buffer to assert output.
        stream = StringIO()
        root.handlers[0].setStream(stream)  # type: ignore[attr-defined]
        with run_context("run_setup"):
            logging.getLogger("test.setup").info("configured")

        payload = json.loads(stream.getvalue().strip())
        assert payload["message"] == "configured"
        assert payload["run_id"] == "run_setup"
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


def test_setup_logging_plain_text_carries_run_id() -> None:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(level=logging.INFO, json=False)
        stream = StringIO()
        root.handlers[0].setStream(stream)  # type: ignore[attr-defined]
        with run_context("run_plain"):
            logging.getLogger("test.plain").info("plain message")

        output = stream.getvalue()
        assert "run_id=run_plain" in output
        assert "plain message" in output
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)
