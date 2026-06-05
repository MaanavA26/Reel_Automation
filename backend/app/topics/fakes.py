"""In-memory `TrendProvider` for hermetic tests (no network).

A factory-style fake (testing-standards: "don't mock what you can fake") that
returns scripted topic ideas per niche and records the calls it received.
Mirrors `app.services.search.fakes.FakeSearchProvider`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.topics.base import TopicIdea


@dataclass
class RecordedDiscover:
    """A single `discover` invocation captured by the fake."""

    niche: str
    limit: int


class FakeTrendProvider:
    """A `TrendProvider` that replays scripted topic ideas.

    Construct with either a flat list of ideas (returned for every niche) or a
    per-niche mapping. Records each call for assertions. Returns at most
    ``limit`` ideas, mirroring a real backend.
    """

    name = "fake"

    def __init__(
        self,
        ideas: Sequence[TopicIdea] | None = None,
        *,
        by_niche: Mapping[str, Sequence[TopicIdea]] | None = None,
    ) -> None:
        self._ideas: list[TopicIdea] = list(ideas or [])
        self._by_niche: dict[str, list[TopicIdea]] = {
            n: list(items) for n, items in (by_niche or {}).items()
        }
        self.calls: list[RecordedDiscover] = []

    async def discover(self, *, niche: str, limit: int = 10) -> list[TopicIdea]:
        self.calls.append(RecordedDiscover(niche=niche, limit=limit))
        ideas = self._by_niche.get(niche, self._ideas)
        return list(ideas[:limit])
