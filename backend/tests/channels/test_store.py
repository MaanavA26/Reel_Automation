"""Tests for `InMemoryChannelStore` and `FakeChannelStore`.

Fully hermetic: async store methods driven via `asyncio.run` (the repo's
no-pytest-asyncio convention, mirroring `tests/media/test_pipeline.py`). Both the
production store and the fake are exercised through the same parametrized cases,
so the fake provably matches real CRUD semantics.
"""

from __future__ import annotations

import asyncio

import pytest

from app.channels.fakes import FakeChannelStore
from app.channels.schemas import Branding, ChannelProfile, NarrativeTone, Platform
from app.channels.store import (
    ChannelNotFoundError,
    ChannelStore,
    DuplicateChannelError,
    InMemoryChannelStore,
    InvalidUpdateError,
)


def _profile(name: str = "Chan", **overrides: object) -> ChannelProfile:
    base: dict[str, object] = {
        "name": name,
        "niche": "applied AI",
        "platforms": [Platform.YOUTUBE_SHORTS],
        "tts_voice_id": "voice_aria",
        "branding": Branding(handle="@chan"),
    }
    base.update(overrides)
    return ChannelProfile(**base)  # type: ignore[arg-type]


# Parametrize over both implementations of the seam so they stay in lockstep.
STORE_FACTORIES = [
    pytest.param(InMemoryChannelStore, id="in_memory"),
    pytest.param(FakeChannelStore, id="fake"),
]


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_both_satisfy_protocol(factory: type) -> None:
    assert isinstance(factory(), ChannelStore)


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_create_then_get(factory: type) -> None:
    store: ChannelStore = factory()
    p = _profile()
    asyncio.run(store.create(p))
    assert asyncio.run(store.get(p.id)) == p


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_get_unknown_returns_none(factory: type) -> None:
    store: ChannelStore = factory()
    assert asyncio.run(store.get("chan_missing")) is None


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_create_duplicate_raises(factory: type) -> None:
    store: ChannelStore = factory()
    p = _profile()
    asyncio.run(store.create(p))
    with pytest.raises(DuplicateChannelError):
        asyncio.run(store.create(p))


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_list_returns_all(factory: type) -> None:
    store: ChannelStore = factory()
    a, b = _profile("A"), _profile("B")
    asyncio.run(store.create(a))
    asyncio.run(store.create(b))
    listed = asyncio.run(store.list())
    assert {p.id for p in listed} == {a.id, b.id}


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_update_applies_changes_and_bumps_updated_at(factory: type) -> None:
    store: ChannelStore = factory()
    p = _profile(tone=NarrativeTone.CASUAL)
    asyncio.run(store.create(p))
    updated = asyncio.run(store.update(p.id, tone=NarrativeTone.ENERGETIC, persona="punchy"))
    assert updated.tone is NarrativeTone.ENERGETIC
    assert updated.persona == "punchy"
    assert updated.id == p.id
    assert updated.created_at == p.created_at
    assert updated.updated_at >= p.updated_at
    # Persisted, not just returned.
    assert asyncio.run(store.get(p.id)) == updated


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_update_unknown_raises(factory: type) -> None:
    store: ChannelStore = factory()
    with pytest.raises(ChannelNotFoundError):
        asyncio.run(store.update("chan_missing", niche="x"))


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.parametrize("field", ["id", "created_at", "updated_at", "nope"])
def test_update_rejects_immutable_or_unknown_field(factory: type, field: str) -> None:
    store: ChannelStore = factory()
    p = _profile()
    asyncio.run(store.create(p))
    with pytest.raises(InvalidUpdateError):
        asyncio.run(store.update(p.id, **{field: "x"}))


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_update_revalidates_against_schema(factory: type) -> None:
    # An empty platforms list violates the schema contract — update must reject it.
    store: ChannelStore = factory()
    p = _profile()
    asyncio.run(store.create(p))
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        asyncio.run(store.update(p.id, platforms=[]))


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_delete_removes(factory: type) -> None:
    store: ChannelStore = factory()
    p = _profile()
    asyncio.run(store.create(p))
    asyncio.run(store.delete(p.id))
    assert asyncio.run(store.get(p.id)) is None


@pytest.mark.parametrize("factory", STORE_FACTORIES)
def test_delete_unknown_raises(factory: type) -> None:
    store: ChannelStore = factory()
    with pytest.raises(ChannelNotFoundError):
        asyncio.run(store.delete("chan_missing"))


# --- Fake-specific behavior: pre-seeding + call recording ---


def test_fake_pre_seeds_profiles() -> None:
    a, b = _profile("A"), _profile("B")
    store = FakeChannelStore([a, b])
    assert asyncio.run(store.get(a.id)) == a
    assert {p.id for p in asyncio.run(store.list())} == {a.id, b.id}


def test_fake_records_calls() -> None:
    a = _profile("A")
    store = FakeChannelStore([a])
    asyncio.run(store.get(a.id))
    asyncio.run(store.get("chan_missing"))
    asyncio.run(store.list())
    asyncio.run(store.update(a.id, niche="new niche"))
    b = _profile("B")
    asyncio.run(store.create(b))
    asyncio.run(store.delete(b.id))

    assert store.calls.got == [a.id, "chan_missing"]
    assert store.calls.listed == 1
    assert store.calls.created == [b.id]
    assert store.calls.deleted == [b.id]
    assert len(store.calls.updated) == 1
    assert store.calls.updated[0].channel_id == a.id
    assert store.calls.updated[0].changes == {"niche": "new niche"}
