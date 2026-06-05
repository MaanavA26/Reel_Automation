"""In-memory `FakeChannelStore` for hermetic tests (no I/O, pre-seedable).

The test double behind the `ChannelStore` seam — the channels-package analogue
of `FakeSearchProvider` / `FakeTTSProvider`. Its distinct purpose (vs just
instantiating `InMemoryChannelStore`) is being **pre-seedable and
call-recording**: a downstream scripting / TTS / SEO test can construct it with a
fixed set of profiles and then assert *which* channels were read, without running
real CRUD lifecycle. It implements the same `ChannelStore` `Protocol`, so it is a
drop-in substitute wherever a store is injected.

The store semantics mirror `InMemoryChannelStore` exactly (same not-found / dup /
invalid-update behavior) so a test that passes against the fake reflects real
behavior; the only additions are seeding and call capture.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.channels.schemas import ChannelProfile
from app.channels.store import (
    _MUTABLE_FIELDS,
    ChannelNotFoundError,
    DuplicateChannelError,
    InvalidUpdateError,
)


@dataclass
class RecordedUpdate:
    """A single `update` invocation captured by the fake."""

    channel_id: str
    changes: dict[str, Any]


@dataclass
class RecordedCalls:
    """Per-method call log the fake exposes for test assertions."""

    created: list[str] = field(default_factory=list)
    got: list[str] = field(default_factory=list)
    listed: int = 0
    updated: list[RecordedUpdate] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


class FakeChannelStore:
    """A `ChannelStore` pre-seedable with profiles and recording every call.

    Construct with an iterable of profiles to seed the store; each call is logged
    on `calls` for assertions. CRUD behavior matches `InMemoryChannelStore`
    (no `asyncio.Lock` — a fake driven from a single test coroutine needs none).
    """

    def __init__(self, profiles: Iterable[ChannelProfile] | None = None) -> None:
        self._profiles: dict[str, ChannelProfile] = {p.id: p for p in (profiles or [])}
        self.calls = RecordedCalls()

    async def create(self, profile: ChannelProfile) -> ChannelProfile:
        self.calls.created.append(profile.id)
        if profile.id in self._profiles:
            raise DuplicateChannelError(profile.id)
        self._profiles[profile.id] = profile
        return profile

    async def get(self, channel_id: str) -> ChannelProfile | None:
        self.calls.got.append(channel_id)
        return self._profiles.get(channel_id)

    async def list(self) -> list[ChannelProfile]:
        self.calls.listed += 1
        return list(self._profiles.values())

    async def update(self, channel_id: str, **changes: Any) -> ChannelProfile:
        self.calls.updated.append(RecordedUpdate(channel_id, dict(changes)))
        for field_name in changes:
            if field_name not in _MUTABLE_FIELDS:
                raise InvalidUpdateError(field_name)
        current = self._profiles.get(channel_id)
        if current is None:
            raise ChannelNotFoundError(channel_id)
        updated = current.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        updated = ChannelProfile.model_validate(updated.model_dump())
        self._profiles[channel_id] = updated
        return updated

    async def delete(self, channel_id: str) -> None:
        self.calls.deleted.append(channel_id)
        if channel_id not in self._profiles:
            raise ChannelNotFoundError(channel_id)
        del self._profiles[channel_id]
