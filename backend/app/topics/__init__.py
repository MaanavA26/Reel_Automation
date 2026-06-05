"""Topic / trend sourcing for the Reel Automation pipeline.

A CLAUDE.md §3.4 future layer (introduced via ADR 0037): where the pipeline gets
fresh, high-potential short-form video topics. Per CLAUDE.md §4 the strict
agent-vs-tool split is preserved — *sourcing* trending topics is deterministic
tool/service work (a `TrendProvider` wraps a trends API), while *ranking and
selection* is an explainable deterministic tool (`selection.py`); neither is an
agent. The judgment of which topic to actually green-light belongs to a future
content-strategy agent that consumes this layer's prioritized output.

The package mirrors the search/LLM/media fabric: a `TrendProvider` Protocol, a
hermetic `FakeTrendProvider`, and an `httpx`-based live adapter — so the whole
layer builds, tests, and showcases standalone.
"""

from __future__ import annotations

from app.topics.base import TopicIdea, TrendProvider
from app.topics.fakes import FakeTrendProvider, RecordedDiscover
from app.topics.live import PROVIDER_NAME, HttpTrendProvider, TrendError
from app.topics.selection import select_topics

__all__ = [
    "PROVIDER_NAME",
    "FakeTrendProvider",
    "HttpTrendProvider",
    "RecordedDiscover",
    "TopicIdea",
    "TrendError",
    "TrendProvider",
    "select_topics",
]
