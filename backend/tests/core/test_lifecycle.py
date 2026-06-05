"""Tests for the async-resource lifecycle contract (ADR 0044).

Covers `CloseOwnedClientMixin`: it must close a client it *created* but never one
that was *injected* (the ``httpx.MockTransport`` test seam / a shared client is
the caller's to close). Hermetic — no network; the client is a real
``httpx.AsyncClient`` over a no-op mock transport. Async paths are driven with
``asyncio.run`` to match the repo's adapter-test convention (no pytest-asyncio).
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.lifecycle import AsyncClosable, CloseOwnedClientMixin


class _Adapter(CloseOwnedClientMixin):
    """Minimal adapter mirroring the production __init__ contract."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()


def _mock_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: httpx.Response(200)))


def test_satisfies_async_closable_protocol() -> None:
    assert isinstance(_Adapter(), AsyncClosable)


def test_aclose_closes_a_created_client() -> None:
    adapter = _Adapter()  # no client injected -> adapter owns it
    assert adapter._owns_client is True

    asyncio.run(adapter.aclose())

    assert adapter._client.is_closed


def test_aclose_does_not_close_an_injected_client() -> None:
    injected = _mock_client()
    adapter = _Adapter(client=injected)  # injected -> caller owns it
    assert adapter._owns_client is False

    asyncio.run(adapter.aclose())

    assert injected.is_closed is False
    asyncio.run(injected.aclose())  # tidy up the test-owned client


def test_async_with_closes_an_owned_client() -> None:
    async def _use() -> httpx.AsyncClient:
        async with _Adapter() as adapter:
            assert adapter._client.is_closed is False
            return adapter._client

    client = asyncio.run(_use())
    assert client.is_closed
