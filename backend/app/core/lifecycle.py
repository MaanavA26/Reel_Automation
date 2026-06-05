"""Async-resource lifecycle contract for long-lived adapters.

The live provider adapters (`app.services.llm.*`, `app.services.search.*`,
`app.services.ingestion.httpx_fetch`, `app.media.*`, the publishing/analytics/
topics adapters) each own a persistent ``httpx.AsyncClient``. Built once per app
(ADR 0044), those clients must be closed on shutdown or every restart leaks the
underlying sockets/file descriptors.

This module defines the single contract for "an adapter that holds a closable
resource" so the composition root can hand the API layer a flat list of things
to drain on shutdown, without reaching through agent internals to find the
clients (which would break the agent/tool encapsulation, CLAUDE.md §4/§10).

Adapters that own a client should:

* implement ``aclose()`` — closing **only** a client they created (an *injected*
  client is owned by the caller, typically a test's ``httpx.MockTransport``, and
  must not be closed here), and
* support ``async with`` via ``__aexit__`` for symmetric local use.

The ``CloseOwnedClientMixin`` provides both from a single ``self._client`` +
``self._owns_client`` pair, so each adapter adds the contract in one line.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class AsyncClosable(Protocol):
    """An async resource that releases what it owns on ``aclose()``.

    Deliberately minimal (just ``aclose``) so unrelated closable resources — e.g.
    a future non-httpx connection pool — satisfy it too. The API lifespan drains
    a ``list[AsyncClosable]`` collected by the composition root.
    """

    async def aclose(self) -> None: ...


class CloseOwnedClientMixin:
    """Adds `aclose()`/`async with` to an adapter that owns an ``httpx.AsyncClient``.

    Expects the subclass ``__init__`` to set two attributes:

    * ``self._client`` — the ``httpx.AsyncClient`` the adapter makes requests on.
    * ``self._owns_client`` — ``True`` iff this adapter *created* that client
      (``client is None`` at construction). When a client is injected (the
      ``httpx.MockTransport`` test seam, or a shared client), the caller owns its
      lifecycle and we must not close it.

    ``aclose()`` is idempotent: closing an already-closed client is a no-op in
    httpx, and re-entry is harmless.
    """

    _client: httpx.AsyncClient
    _owns_client: bool

    async def aclose(self) -> None:
        """Close the underlying client if (and only if) this adapter created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CloseOwnedClientMixin:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
