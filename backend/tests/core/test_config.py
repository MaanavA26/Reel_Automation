"""Tests for `Settings` parse-time validation (ADR 0055 retry knobs).

Hermetic: `Settings` is constructed directly with explicit values (no env, no
`.env` file dependency — defaults asserted are the class defaults). The point
of these tests is that an invalid retry configuration fails loud at *parse*
time with a clear message, never later inside `_build_router` or mid-run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_retry_defaults_are_off_and_sane() -> None:
    s = Settings()
    assert s.llm_retry_max_attempts == 1  # retry disabled by default
    assert s.llm_retry_max_delay >= s.llm_retry_base_delay


def test_retry_max_attempts_below_one_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_retry_max_attempts=0)


def test_negative_delays_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_retry_base_delay=-1.0)
    with pytest.raises(ValidationError):
        Settings(llm_retry_max_delay=-1.0)


def test_backoff_factor_below_one_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_retry_backoff_factor=0.5)


def test_inverted_delay_ladder_rejected() -> None:
    with pytest.raises(ValidationError, match="llm_retry_max_delay"):
        Settings(llm_retry_base_delay=30.0, llm_retry_max_delay=5.0)


def test_aeneas_python_bin_defaults_to_none() -> None:
    # Unset by default (ADR 0062/0063): the composition root leaves
    # MediaPipeline's word_aligner unset unless an operator opts in.
    assert Settings().aeneas_python_bin is None


# --- Hermetic env-file isolation (#156) ---------------------------------------


def test_hermetic_settings_construction_ignores_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for #156: a `.env` file must never leak into hermetic tests.

    pydantic-settings resolves a relative `env_file` against the CWD at
    instantiation time, so without the conftest neutralization the `.env`
    written here would populate `stock_api_key` and this test would fail —
    exactly how a developer machine's real keys broke key-absent wiring tests.
    """
    (tmp_path / ".env").write_text("REEL_AUTOMATION_STOCK_API_KEY=leaked-from-file\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().stock_api_key.get_secret_value() == ""


def test_process_env_vars_still_reach_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # The #156 isolation disables only the env *file* source: real environment
    # variables (the production configuration path CI and operators use) must
    # keep flowing into Settings.
    monkeypatch.setenv("REEL_AUTOMATION_STOCK_API_KEY", "from-process-env")
    assert Settings().stock_api_key.get_secret_value() == "from-process-env"
