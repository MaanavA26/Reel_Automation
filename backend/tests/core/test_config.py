"""Tests for `Settings` parse-time validation (ADR 0055 retry knobs).

Hermetic: `Settings` is constructed directly with explicit values (no env, no
`.env` file dependency — defaults asserted are the class defaults). The point
of these tests is that an invalid retry configuration fails loud at *parse*
time with a clear message, never later inside `_build_router` or mid-run.
"""

from __future__ import annotations

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
