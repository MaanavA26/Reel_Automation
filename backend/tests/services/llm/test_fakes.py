"""Tests for the in-memory FakeProvider (M2)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.services.llm.fakes import FakeProvider


class _A(BaseModel):
    x: int


class _B(BaseModel):
    y: int


def test_fake_replays_in_order_and_records() -> None:
    fake = FakeProvider([_A(x=1), _A(x=2)])
    a1 = asyncio.run(fake.complete_structured(model="m", system="s", prompt="p", schema=_A))
    a2 = asyncio.run(fake.complete_structured(model="m", system="s", prompt="p2", schema=_A))
    assert (a1.x, a2.x) == (1, 2)
    assert [c.prompt for c in fake.calls] == ["p", "p2"]


def test_fake_exhausted_raises() -> None:
    fake = FakeProvider([])
    with pytest.raises(AssertionError):
        asyncio.run(fake.complete_structured(model="m", system="s", prompt="p", schema=_A))


def test_fake_schema_mismatch_raises() -> None:
    fake = FakeProvider([_A(x=1)])
    with pytest.raises(AssertionError):
        asyncio.run(fake.complete_structured(model="m", system="s", prompt="p", schema=_B))
