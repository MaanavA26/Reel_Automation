"""Tests for the LLM resilience layer (retry decorator + fallback helper).

Fully hermetic and deterministic: a local ``_FlakyProvider`` raises a chosen
exception N times then replays a scripted response (the existing ``FakeProvider``
only replays — it never raises — so the raising fake lives here, not in
``fakes.py``). Backoff is asserted without real time passing via an injected
no-op sleeper that records the requested delays.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.services.llm.base import ModelRole, StructuredT
from app.services.llm.fakes import FakeProvider
from app.services.llm.resilience import (
    ResilienceError,
    ResilientModelProvider,
    ResilientRouter,
    RetryConfig,
    complete_with_fallback,
)
from app.services.llm.router import ModelChoice, ModelRouter


class _Out(BaseModel):
    value: str


class _Transient(RuntimeError):
    """Stand-in for a provider's transient (retryable) error."""


class _Permanent(RuntimeError):
    """Stand-in for a non-transient error that must not be retried."""


class _FlakyProvider:
    """A `ModelProvider` that raises ``error`` for the first ``fail_times`` calls.

    After the failures it returns ``success`` (an instance of the requested
    schema). Records the number of calls so tests assert the retry count exactly.
    """

    name = "flaky"

    def __init__(self, *, fail_times: int, error: Exception, success: BaseModel) -> None:
        self._fail_times = fail_times
        self._error = error
        self._success = success
        self.calls = 0

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        assert isinstance(self._success, schema)
        return self._success


class _RecordingSleeper:
    """A no-op async sleeper that records the delays it was asked to wait."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _call(provider: ResilientModelProvider) -> _Out:
    return asyncio.run(provider.complete_structured(model="m", system="s", prompt="p", schema=_Out))


# --- RetryConfig --------------------------------------------------------------


def test_retry_config_rejects_bad_values() -> None:
    with pytest.raises(ResilienceError):
        RetryConfig(max_attempts=0)
    with pytest.raises(ResilienceError):
        RetryConfig(backoff_factor=0.5)
    with pytest.raises(ResilienceError):
        RetryConfig(retry_on=())


def test_delay_schedule_is_exponential_and_capped() -> None:
    cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, max_delay=5.0)
    assert [cfg.delay_for(i) for i in range(4)] == [1.0, 2.0, 4.0, 5.0]  # last capped


# --- ResilientModelProvider: retry -------------------------------------------


def test_retries_transient_then_succeeds() -> None:
    inner = _FlakyProvider(fail_times=2, error=_Transient(), success=_Out(value="ok"))
    sleeper = _RecordingSleeper()
    provider = ResilientModelProvider(
        inner,
        RetryConfig(max_attempts=3, base_delay=1.0, retry_on=(_Transient,)),
        sleep=sleeper,
    )

    out = _call(provider)

    assert out.value == "ok"
    assert inner.calls == 3  # 2 failures + 1 success
    assert sleeper.delays == [1.0, 2.0]  # backoff before each of the 2 retries


def test_raises_last_error_after_exhaustion() -> None:
    boom = _Transient("still failing")
    inner = _FlakyProvider(fail_times=5, error=boom, success=_Out(value="never"))
    provider = ResilientModelProvider(
        inner,
        RetryConfig(max_attempts=3, retry_on=(_Transient,)),
        sleep=_RecordingSleeper(),
    )

    with pytest.raises(_Transient) as exc_info:
        _call(provider)

    assert exc_info.value is boom  # the real provider error, not a wrapper
    assert inner.calls == 3  # bounded: exactly max_attempts


def test_non_transient_error_is_not_retried() -> None:
    inner = _FlakyProvider(fail_times=1, error=_Permanent(), success=_Out(value="x"))
    sleeper = _RecordingSleeper()
    provider = ResilientModelProvider(
        inner,
        RetryConfig(max_attempts=3, retry_on=(_Transient,)),
        sleep=sleeper,
    )

    with pytest.raises(_Permanent):
        _call(provider)

    assert inner.calls == 1  # raised immediately, no retry


# --- ResilientModelProvider: retry_if instance narrowing ----------------------


def test_retry_if_false_propagates_matching_type_immediately() -> None:
    # The 429-vs-401 case: one exception type, but the instance is permanent.
    boom = _Transient("HTTP 401")
    inner = _FlakyProvider(fail_times=1, error=boom, success=_Out(value="x"))
    sleeper = _RecordingSleeper()
    provider = ResilientModelProvider(
        inner,
        RetryConfig(max_attempts=3, retry_on=(_Transient,), retry_if=lambda exc: False),
        sleep=sleeper,
    )

    with pytest.raises(_Transient) as exc_info:
        _call(provider)

    assert exc_info.value is boom  # the real provider error, not a wrapper
    assert inner.calls == 1  # no retry
    assert sleeper.delays == []  # and no backoff wait


def test_retry_if_true_preserves_the_retry_path() -> None:
    inner = _FlakyProvider(fail_times=2, error=_Transient(), success=_Out(value="ok"))
    provider = ResilientModelProvider(
        inner,
        RetryConfig(
            max_attempts=3, base_delay=1.0, retry_on=(_Transient,), retry_if=lambda exc: True
        ),
        sleep=_RecordingSleeper(),
    )

    out = _call(provider)

    assert out.value == "ok"
    assert inner.calls == 3  # 2 transient failures + 1 success, as without retry_if


def test_max_attempts_one_disables_retry() -> None:
    inner = _FlakyProvider(fail_times=1, error=_Transient(), success=_Out(value="x"))
    provider = ResilientModelProvider(
        inner,
        RetryConfig(max_attempts=1, retry_on=(_Transient,)),
        sleep=_RecordingSleeper(),
    )

    with pytest.raises(_Transient):
        _call(provider)

    assert inner.calls == 1


def test_name_delegates_to_inner_provider() -> None:
    inner = FakeProvider([_Out(value="ok")])
    provider = ResilientModelProvider(inner, sleep=_RecordingSleeper())
    assert provider.name == "fake"  # transparent for router registration


# --- Fallback helper ----------------------------------------------------------


def _router(
    *,
    planning: object,
    fallback: object | None,
) -> ModelRouter:
    providers: dict[str, object] = {"primary": planning}
    policy = {ModelRole.PLANNING: ModelChoice("primary", "primary-model")}
    if fallback is not None:
        providers["fb"] = fallback
        policy[ModelRole.FALLBACK] = ModelChoice("fb", "fb-model")
    return ModelRouter(providers=providers, policy=policy)  # type: ignore[arg-type]


def _fallback_call(router: ModelRouter, *, role: ModelRole = ModelRole.PLANNING) -> _Out:
    return asyncio.run(
        complete_with_fallback(router, role=role, system="s", prompt="p", schema=_Out)
    )


def test_fallback_engages_after_primary_exhaustion() -> None:
    # Primary is a retry decorator that exhausts; fallback is a healthy provider.
    failing_inner = _FlakyProvider(fail_times=9, error=_Transient(), success=_Out(value="x"))
    primary = ResilientModelProvider(
        failing_inner,
        RetryConfig(max_attempts=3, retry_on=(_Transient,)),
        sleep=_RecordingSleeper(),
    )
    fallback = FakeProvider([_Out(value="from-fallback")])
    router = _router(planning=primary, fallback=fallback)

    out = _fallback_call(router)

    assert out.value == "from-fallback"
    assert failing_inner.calls == 3  # primary retried to exhaustion first
    assert len(fallback.calls) == 1  # exactly one fallback hop (no retry-of-retry)
    assert fallback.calls[0].model == "fb-model"


def test_no_fallback_reraises_primary_error() -> None:
    boom = _Transient("primary down")
    primary = _FlakyProvider(fail_times=1, error=boom, success=_Out(value="x"))
    router = _router(planning=primary, fallback=None)  # no FALLBACK in policy

    with pytest.raises(_Transient) as exc_info:
        _fallback_call(router)

    assert exc_info.value is boom  # the primary error, not an UnknownRoleError


def test_self_fallback_guard_reraises_primary_error() -> None:
    # FALLBACK resolves to the same (provider, model) as PLANNING -> no useless hop.
    boom = _Transient("down")
    primary = _FlakyProvider(fail_times=1, error=boom, success=_Out(value="x"))
    router = ModelRouter(
        providers={"primary": primary},  # type: ignore[dict-item]
        policy={
            ModelRole.PLANNING: ModelChoice("primary", "primary-model"),
            ModelRole.FALLBACK: ModelChoice("primary", "primary-model"),
        },
    )

    with pytest.raises(_Transient) as exc_info:
        _fallback_call(router)

    assert exc_info.value is boom
    assert primary.calls == 1  # not called a second time as its own fallback


def test_primary_success_skips_fallback() -> None:
    primary = FakeProvider([_Out(value="from-primary")])
    fallback = FakeProvider([_Out(value="from-fallback")])
    router = _router(planning=primary, fallback=fallback)

    out = _fallback_call(router)

    assert out.value == "from-primary"
    assert len(fallback.calls) == 0  # fallback untouched on the happy path


def test_resilient_router_complete_delegates_to_fallback() -> None:
    primary = _FlakyProvider(fail_times=1, error=_Transient(), success=_Out(value="x"))
    fallback = FakeProvider([_Out(value="from-fallback")])
    router = ResilientRouter(_router(planning=primary, fallback=fallback))

    out = asyncio.run(router.complete(role=ModelRole.PLANNING, system="s", prompt="p", schema=_Out))

    assert out.value == "from-fallback"
