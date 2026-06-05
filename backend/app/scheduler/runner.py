"""`BatchRunner` — produce a batch of videos concurrently, with error isolation.

The execution half of the unattended loop. It pulls topics (from a `TopicQueue`
or any iterable) and runs an **injected** "produce one video" coroutine for each,
under a concurrency cap, isolating per-topic failures so one bad topic never
aborts the batch.

The produce step is injected as ``Produce = Callable[[str], Awaitable[T]]`` rather
than importing a concrete pipeline, so the runner is fully decoupled from the
real `VideoPipeline` (a sibling component) — tests inject a fake, and wiring the
real pipeline is a follow-up (see the package ``__init__`` docstring / ADR 0034).
This mirrors `JobStore`'s workflow-agnostic `JobRunner` seam (ADR 0031).

**No clock, no sleeping.** The runner only awaits the injected callable under an
`asyncio.Semaphore`; it does no timing. The "wait until the next scheduled time,
then run a batch" driver loop — which composes `Schedule` + `TopicQueue` +
`BatchRunner` and would inject the clock + sleeper — is the deferred process
runner (ADR 0034). Keeping timing out of the runner is what makes batch tests
require no real waiting.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: The injected "produce one video" step: topic -> awaitable result of type ``T``.
#: Decoupled from the real `VideoPipeline` so it is trivially fakeable in tests.
Produce = Callable[[str], Awaitable[T]]


@dataclass(frozen=True)
class TopicResult(Generic[T]):
    """The outcome of producing one topic — exactly one of ``value``/``error`` set.

    ``error`` holds the *captured* exception (never re-raised by the batch) so a
    single failure is observable without stopping the rest — mirroring `JobStore`'s
    exception-to-terminal-state contract.
    """

    topic: str
    value: T | None = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class BatchResult(Generic[T]):
    """Aggregated outcome of one batch, in submission order.

    ``results`` preserves the order topics were submitted (not completion order),
    so a caller can correlate results to inputs deterministically.
    """

    results: list[TopicResult[T]] = field(default_factory=list)

    @property
    def succeeded(self) -> list[TopicResult[T]]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[TopicResult[T]]:
        return [r for r in self.results if not r.ok]


class BatchRunner(Generic[T]):
    """Run an injected produce-one-video coroutine across a batch of topics.

    Concurrency is bounded by ``max_concurrency`` (an `asyncio.Semaphore` created
    *inside* `run_batch` so it always binds to the running event loop — a
    semaphore built in ``__init__`` would bind to whichever loop existed at
    construction, breaking tests that call `asyncio.run` on a fresh loop). Each
    topic is produced in isolation: an exception is captured into its
    `TopicResult.error` rather than propagated, so the batch always runs to
    completion and reports every outcome.
    """

    def __init__(self, produce: Produce[T], *, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._produce = produce
        self._max_concurrency = max_concurrency

    async def run_batch(self, topics: Iterable[str]) -> BatchResult[T]:
        """Produce every topic concurrently (capped) and return all outcomes.

        Returns a `BatchResult` whose ``results`` are in submission order. Never
        raises for a per-topic failure (those are captured); only a programming
        error in the runner itself would propagate.
        """
        batch = list(topics)
        if not batch:
            return BatchResult()

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _run_one(topic: str) -> TopicResult[T]:
            async with semaphore:
                try:
                    value = await self._produce(topic)
                except Exception as exc:  # isolate: one failure must not stop the batch
                    logger.exception("producing topic %r failed", topic)
                    return TopicResult(topic=topic, error=exc)
                return TopicResult(topic=topic, value=value)

        # `return_exceptions=False` is safe: `_run_one` never raises (it captures),
        # so gather yields one `TopicResult` per topic in submission order.
        results = await asyncio.gather(*(_run_one(t) for t in batch))
        return BatchResult(results=list(results))
