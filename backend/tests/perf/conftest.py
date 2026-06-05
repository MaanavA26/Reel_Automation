"""Pytest configuration local to the performance / benchmark harness.

Registers the ``perf`` marker so the benchmark modules in this package can be
selected explicitly (``pytest -m perf``) without tripping pytest's
``--strict-markers``-style unknown-marker warning, and without editing the
shared ``pyproject.toml`` (out of scope for this component).

Why a *local* ``conftest`` and not ``pyproject``: the project's default
``addopts = "-m 'not integration'"`` already deselects anything marked
``integration``. The perf benchmarks ride that existing deselection by *also*
carrying ``@pytest.mark.integration`` (see the module docstrings), so the
default ``pytest -q`` suite never runs them. The ``perf`` marker added here is
the *positive* selector — ``pytest -m perf`` runs only the benchmarks and, by
overriding the addopts ``-m`` on the CLI, deliberately excludes the network-gated
``integration``-only live tests that a bare ``-m integration`` would drag in.

Registering a marker line is global-safe: it only suppresses a warning for a
marker no other suite uses, with no behavioral side effects elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

# Key under which benchmarks stash rendered timing tables on the pytest config,
# so ``pytest_terminal_summary`` can print them even when stdout is captured on a
# passing run (the default ``-q`` behaviour hides ``print`` output).
_PERF_TABLES_KEY = "_perf_timing_tables"


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``perf`` marker used by this package's benchmark modules."""
    config.addinivalue_line(
        "markers",
        "perf: informational, offline timing benchmark (run with -m perf; not a pass/fail gate)",
    )
    setattr(config, _PERF_TABLES_KEY, [])


@pytest.fixture
def record_perf_table(request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Return a sink that queues a rendered timing table for the run summary.

    A benchmark builds its table string via ``harness.render_table`` and passes
    it here; the table is printed once, after the run, by
    ``pytest_terminal_summary`` — so it survives stdout capture and appears even
    on a green ``pytest -m perf`` run (no ``-s`` needed).
    """
    tables: list[str] = getattr(request.config, _PERF_TABLES_KEY)

    def _record(table: str) -> None:
        tables.append(table)

    return _record


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print any recorded benchmark tables at the end of the run."""
    tables: list[str] = getattr(terminalreporter.config, _PERF_TABLES_KEY, [])
    if not tables:
        return
    terminalreporter.write_sep("=", "perf benchmarks")
    for table in tables:
        terminalreporter.write_line(table)
