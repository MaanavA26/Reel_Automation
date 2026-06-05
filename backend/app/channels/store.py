"""`ChannelStore` seam + the in-memory production implementation.

A `ChannelStore` is the persistence seam for `ChannelProfile`s — the CRUD surface
the operator (and a future channels API/CLI) uses to register and address the
channels it runs. Per CLAUDE.md §4 this is a deterministic **service/tool**, not
an agent: it owns addressable storage and lifecycle bookkeeping (the
``updated_at`` bump on update), with no reasoning.

**Protocol-first, mirroring the media/search fabric, not `JobStore`.** Unlike the
single-implementation `JobStore`, the store is a `@runtime_checkable` `Protocol`
(`TTSProvider` / `SearchProvider` idiom) so it has a real seam: `InMemoryChannelStore`
is the production default and `FakeChannelStore` (see `fakes`) is the
pre-seedable, call-recording test double that downstream scripting/TTS/SEO tests
inject. A durable backend (e.g. `SqlChannelStore`) is a documented follow-up — it
implements the same `Protocol` and drops in without touching consumers (ADR 0042).

**Async by design.** The in-memory store has no real I/O, but the seam is async
(mirroring `JobStore` and anticipating the deferred durable backend, which would
be async) so adopting persistence later is not a signature-breaking change.
Mutations are serialized by one `asyncio.Lock`, exactly as `JobStore` does;
single-event-loop today, so the lock is for honesty/future-proofing.

**Single-process, non-durable by design.** Profiles live in a plain dict in this
process's memory. Sufficient for the development/demo target and hermetic tests;
a restart loses all profiles and a profile created on one worker is invisible to
another. The durable, shared-state backend is deferred — see ADR 0042 (the same
deferral `JobStore` makes in ADR 0031).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.channels.schemas import ChannelProfile

logger = logging.getLogger(__name__)

# The mutable fields a partial `update` may set. ``id``/``created_at`` are
# immutable identity/provenance; ``updated_at`` is store-owned (bumped on every
# update). A key outside this set is rejected, so a typo cannot silently no-op or
# mint an unexpected attribute (the validation runs through the schema regardless).
_MUTABLE_FIELDS = frozenset(ChannelProfile.model_fields) - {
    "id",
    "created_at",
    "updated_at",
}


class ChannelStoreError(Exception):
    """Base class for channel-store errors."""


class ChannelNotFoundError(ChannelStoreError):
    """Raised when an operation addresses a channel id that does not exist."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(f"channel not found: {channel_id}")
        self.channel_id = channel_id


class DuplicateChannelError(ChannelStoreError):
    """Raised when creating a channel whose id already exists in the store."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(f"channel already exists: {channel_id}")
        self.channel_id = channel_id


class InvalidUpdateError(ChannelStoreError):
    """Raised when an `update` names a field that is absent or not mutable."""

    def __init__(self, field: str) -> None:
        super().__init__(f"field is not updatable: {field}")
        self.field = field


@runtime_checkable
class ChannelStore(Protocol):
    """CRUD seam over `ChannelProfile`s, addressable by `ChannelProfile.id`.

    The contract every backend (the in-memory default, the deferred durable one,
    the test fake) implements. Methods are async so the seam survives a future
    I/O-bound backend unchanged. ``get`` returns ``None`` for an unknown id (a
    not-found *query* is not an error); the mutating ``update``/``delete`` raise
    `ChannelNotFoundError` (operating on a missing id *is* a caller error).
    """

    async def create(self, profile: ChannelProfile) -> ChannelProfile: ...

    async def get(self, channel_id: str) -> ChannelProfile | None: ...

    async def list(self) -> list[ChannelProfile]: ...

    async def update(self, channel_id: str, **changes: Any) -> ChannelProfile: ...

    async def delete(self, channel_id: str) -> None: ...


class InMemoryChannelStore:
    """Process-local `ChannelStore`, keyed by `ChannelProfile.id`.

    The production default. Not durable and not cross-worker (see the module
    docstring / ADR 0042). All mutations are serialized by a single
    `asyncio.Lock`; the store runs on one event loop, so the lock is for
    honesty/future-proofing rather than to tame real contention — the same stance
    `JobStore` takes.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, ChannelProfile] = {}
        self._lock = asyncio.Lock()

    async def create(self, profile: ChannelProfile) -> ChannelProfile:
        """Register a new profile and return the stored snapshot.

        The profile carries its own id (minted by the schema). Re-creating an
        existing id raises `DuplicateChannelError` rather than silently
        overwriting — `update` is the explicit edit path.
        """
        async with self._lock:
            if profile.id in self._profiles:
                raise DuplicateChannelError(profile.id)
            self._profiles[profile.id] = profile
        logger.info("created channel profile %s", profile.id)
        return profile

    async def get(self, channel_id: str) -> ChannelProfile | None:
        """Return the current snapshot for ``channel_id``, or ``None`` if unknown.

        ``None`` is the not-found signal; translating it into an HTTP 404 (or a
        CLI message) is a caller concern — this service stays transport-agnostic.
        """
        async with self._lock:
            return self._profiles.get(channel_id)

    async def list(self) -> list[ChannelProfile]:
        """Return a snapshot list of all stored profiles (insertion order)."""
        async with self._lock:
            return list(self._profiles.values())

    async def update(self, channel_id: str, **changes: Any) -> ChannelProfile:
        """Apply a partial update to a profile and return the new snapshot.

        Only mutable fields (`_MUTABLE_FIELDS`) may be set; naming ``id`` /
        ``created_at`` / ``updated_at`` or an unknown field raises
        `InvalidUpdateError`. The store owns the ``updated_at`` bump (the schema
        carries the field; the store sets it), mirroring `JobStore`'s
        ``model_copy(update={"updated_at": ...})``. The change is re-validated
        through the schema, so an out-of-contract value (e.g. an empty
        ``platforms``) is rejected before it is stored.
        """
        for field in changes:
            if field not in _MUTABLE_FIELDS:
                raise InvalidUpdateError(field)
        async with self._lock:
            current = self._profiles.get(channel_id)
            if current is None:
                raise ChannelNotFoundError(channel_id)
            updated = current.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
            # Re-validate: model_copy bypasses validators, so round-trip the
            # changed model through the schema to enforce the field contracts.
            updated = ChannelProfile.model_validate(updated.model_dump())
            self._profiles[channel_id] = updated
        logger.info("updated channel profile %s", channel_id)
        return updated

    async def delete(self, channel_id: str) -> None:
        """Remove a profile. Raises `ChannelNotFoundError` for an unknown id."""
        async with self._lock:
            if channel_id not in self._profiles:
                raise ChannelNotFoundError(channel_id)
            del self._profiles[channel_id]
        logger.info("deleted channel profile %s", channel_id)
