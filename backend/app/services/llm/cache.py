"""Response-caching decorator for the model fabric (services layer).

`CachingModelProvider` *wraps* any `ModelProvider` (decorator pattern —
composition, not inheritance) and memoizes `complete_structured` results keyed
by a stable hash of ``(model, system, prompt, schema-identity)``. A cache hit
skips the wrapped call entirely; a miss calls through and populates the cache.

Per CLAUDE.md §4, this is deterministic *tool/service* work (a transparent
performance/cost wrapper), not an agent: it makes no judgments and contains no
provider-specific code — it composes around the provider-neutral
`ModelProvider` contract (ADR 0003).

**Correctness caveat — opt-in only.** Caching assumes the wrapped model is
effectively deterministic for a given input. Real LLMs are not guaranteed to be:
even at ``temperature=0`` a provider may return different output across calls.
Wrapping a provider in this cache therefore *trades freshness for cost/latency*
— two identical requests return the first response, not a fresh sample. Use it
only where that trade is acceptable (e.g. dev loops, idempotent replays, batch
fan-out over repeated prompts). It is not applied by default; a caller opts in
by composing it around a provider explicitly.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel

from app.services.llm.base import ModelProvider, StructuredT


@runtime_checkable
class CacheBackend(Protocol):
    """Pluggable storage for cached structured responses.

    Implementations map an opaque string ``key`` to a stored ``BaseModel``.
    The backend never inspects the key or value beyond identity storage; key
    construction and schema typing are the caching provider's concern.
    """

    def get(self, key: str) -> BaseModel | None:
        """Return the stored model for ``key``, or ``None`` on a miss."""
        ...

    def set(self, key: str, value: BaseModel) -> None:
        """Store ``value`` under ``key`` (overwriting any existing entry)."""
        ...


class InMemoryCacheBackend:
    """A process-local `CacheBackend` backed by a stdlib ``OrderedDict``.

    With ``max_size=None`` (the default) the cache is unbounded. With a positive
    ``max_size`` it behaves as an LRU: ``get`` and ``set`` mark a key as most
    recently used, and inserting beyond capacity evicts the least recently used
    entry. State lives only in the process — nothing is persisted.
    """

    def __init__(self, *, max_size: int | None = None) -> None:
        if max_size is not None and max_size <= 0:
            raise ValueError("max_size must be a positive int or None (unbounded)")
        self._max_size = max_size
        self._store: OrderedDict[str, BaseModel] = OrderedDict()

    def get(self, key: str) -> BaseModel | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: BaseModel) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        if self._max_size is not None:
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


def _cache_key(*, model: str, system: str, prompt: str, schema: type[BaseModel]) -> str:
    """Build a stable SHA-256 key over the request's cache-relevant identity.

    Schema identity is captured by *both* a qualified name and the full JSON
    Schema, so two distinct schemas (even with identical fields) — and any change
    to a schema's shape — produce distinct keys, never a cross-schema collision.
    ``sort_keys`` makes the JSON-Schema serialization order-independent.
    """
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "schema_name": f"{schema.__module__}.{schema.__qualname__}",
        "schema_json": schema.model_json_schema(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingModelProvider:
    """A `ModelProvider` decorator that memoizes structured responses.

    Wraps any `ModelProvider` plus a `CacheBackend` (defaulting to an unbounded
    in-memory backend). On `complete_structured` it computes a stable key from
    ``(model, system, prompt, schema-identity)``; a hit returns the cached value
    without touching the wrapped provider, a miss calls through and stores the
    result.

    Returned values are defensively deep-copied (`model_copy`) on both store and
    return, so a caller mutating a result cannot corrupt the cached entry or a
    later hit. Exceptions from the wrapped provider are never cached — a failed
    call leaves the key absent, so the next identical call retries.

    Non-goal: concurrent identical misses are not de-duplicated (no in-flight
    "stampede" lock). Both would call the wrapped provider; this is acceptable
    for the cache's cost-saving intent and avoids speculative surface
    (CLAUDE.md §7).
    """

    def __init__(
        self,
        wrapped: ModelProvider,
        *,
        backend: CacheBackend | None = None,
    ) -> None:
        self._wrapped = wrapped
        # Explicit None check, not `backend or ...`: an empty InMemoryCacheBackend
        # is falsy (``__len__`` returns 0), which would discard a caller's backend.
        self._backend: CacheBackend = InMemoryCacheBackend() if backend is None else backend
        self.name = f"cached({wrapped.name})"

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        key = _cache_key(model=model, system=system, prompt=prompt, schema=schema)

        cached = self._backend.get(key)
        if cached is not None:
            # Key includes schema identity, so a hit is always this schema type.
            return cast(StructuredT, cached.model_copy(deep=True))

        # Miss: call through. A raised exception propagates and is *not* cached.
        result = await self._wrapped.complete_structured(
            model=model, system=system, prompt=prompt, schema=schema
        )
        self._backend.set(key, result.model_copy(deep=True))
        return result
