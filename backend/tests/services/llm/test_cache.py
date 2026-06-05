"""Tests for the LLM response-cache decorator (`CachingModelProvider`).

Fully hermetic: every test wraps a `FakeProvider` and asserts on the recorded
calls, so a broken cache fails loudly (a missing hit re-invokes the fake and,
when it is seeded with a single response, trips ``FakeProvider exhausted``).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.services.llm.cache import (
    CachingModelProvider,
    InMemoryCacheBackend,
    _cache_key,
)
from app.services.llm.fakes import FakeProvider


class _A(BaseModel):
    x: int


class _B(BaseModel):
    x: int  # same field name as _A on purpose: identity must still differ


class _Boom(Exception):
    pass


class _RaisingProvider:
    """A `ModelProvider` whose first call raises, later calls succeed."""

    name = "raising"

    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(
        self, *, model: str, system: str, prompt: str, schema: type[_A]
    ) -> _A:
        self.calls += 1
        if self.calls == 1:
            raise _Boom("first call fails")
        return schema(x=99)


def _call(provider: CachingModelProvider, prompt: str, schema: type[BaseModel]) -> BaseModel:
    return asyncio.run(
        provider.complete_structured(model="m", system="s", prompt=prompt, schema=schema)
    )


def test_second_identical_request_is_served_from_cache() -> None:
    # Seeded with exactly ONE response: a cache miss on the 2nd call would
    # exhaust the fake and raise, so this asserts the hit path is taken.
    fake = FakeProvider([_A(x=1)])
    cached = CachingModelProvider(fake)

    first = _call(cached, "p", _A)
    second = _call(cached, "p", _A)

    assert first == second == _A(x=1)
    assert len(fake.calls) == 1  # underlying provider called exactly once


def test_distinct_prompt_is_a_distinct_key() -> None:
    fake = FakeProvider([_A(x=1), _A(x=2)])
    cached = CachingModelProvider(fake)

    assert _call(cached, "p1", _A) == _A(x=1)
    assert _call(cached, "p2", _A) == _A(x=2)
    assert len(fake.calls) == 2


def test_distinct_schema_is_a_distinct_key() -> None:
    # _A and _B have identical fields; only their identity differs.
    fake = FakeProvider([_A(x=1), _B(x=1)])
    cached = CachingModelProvider(fake)

    assert isinstance(_call(cached, "p", _A), _A)
    assert isinstance(_call(cached, "p", _B), _B)
    assert len(fake.calls) == 2
    assert _cache_key(model="m", system="s", prompt="p", schema=_A) != _cache_key(
        model="m", system="s", prompt="p", schema=_B
    )


def test_exceptions_are_not_cached() -> None:
    provider = _RaisingProvider()
    cached = CachingModelProvider(provider)

    with pytest.raises(_Boom):
        _call(cached, "p", _A)

    # The failed key must be absent, so the retry calls through and succeeds.
    result = _call(cached, "p", _A)
    assert result == _A(x=99)
    assert provider.calls == 2


def test_returned_value_is_isolated_from_cache() -> None:
    fake = FakeProvider([_A(x=1)])
    cached = CachingModelProvider(fake)

    first = asyncio.run(cached.complete_structured(model="m", system="s", prompt="p", schema=_A))
    first.x = 12345  # mutate the caller's copy

    second = asyncio.run(cached.complete_structured(model="m", system="s", prompt="p", schema=_A))
    assert second.x == 1  # cached entry untouched by the mutation


def test_lru_eviction() -> None:
    backend = InMemoryCacheBackend(max_size=1)
    # 3 responses: p1 (stored), p2 (evicts p1), then p1 again (re-fetched).
    fake = FakeProvider([_A(x=1), _A(x=2), _A(x=3)])
    cached = CachingModelProvider(fake, backend=backend)

    assert _call(cached, "p1", _A) == _A(x=1)
    assert _call(cached, "p2", _A) == _A(x=2)  # evicts p1
    assert len(backend) == 1
    assert _call(cached, "p1", _A) == _A(x=3)  # p1 was evicted -> re-fetched
    assert len(fake.calls) == 3


def test_in_memory_backend_rejects_non_positive_max_size() -> None:
    with pytest.raises(ValueError):
        InMemoryCacheBackend(max_size=0)


def test_name_reflects_wrapped_provider() -> None:
    cached = CachingModelProvider(FakeProvider([]))
    assert cached.name == "cached(fake)"
