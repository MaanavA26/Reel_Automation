"""Shared pytest configuration: hermetic isolation from ``backend/.env`` (#156).

pydantic-settings reads ``Settings.model_config["env_file"]`` at *instantiation*
time, so any hermetic test that constructs ``Settings(...)`` directly silently
absorbs whatever real keys a developer's ``backend/.env`` carries for fields the
test does not pass. Key-absent wiring tests (Kokoro-only closable counts,
"no stock key wires nothing", missing-provider-key guards) then fail spuriously
on a machine configured for live runs, while CI — which has no ``.env`` — stays
green. Bitten twice; see issue #156.

The neutralization below runs once, at conftest *import* time — before pytest
imports any test module — so module-scope constructions (e.g.
``tests/services/llm/test_providers.py``'s ``_KEYED_SETTINGS``) are covered
too, not just test-function-scoped ones. Only the env *file* source is
disabled: process environment variables (``REEL_AUTOMATION_*``, e.g. via
``monkeypatch.setenv``) keep working exactly as before, so tests that
legitimately assert env-var reading are unaffected. Production behavior is
untouched — this module is test-only, and the app's ``get_settings()`` still
reads ``.env`` when the server/CLI runs.

Live integration tests keep their documented workflow ("fill in ``.env``, run
``pytest -m integration``"): the autouse fixture below restores the real
env-file source for the duration of any test carrying the ``integration``
marker, then re-neutralizes it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import Settings

# The production env-file source (".env"), stashed so integration-marked tests
# can temporarily restore it.
_ENV_FILE = Settings.model_config.get("env_file")

# Hermetic default: every Settings(...) constructed by the suite is
# env-file-blind (pydantic-settings consults model_config at instantiation, so
# mutating it here governs all later constructions, including module-scope ones).
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def _restore_env_file_for_integration(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give ``integration``-marked tests back the real ``.env`` file source."""
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    Settings.model_config["env_file"] = _ENV_FILE
    try:
        yield
    finally:
        Settings.model_config["env_file"] = None
