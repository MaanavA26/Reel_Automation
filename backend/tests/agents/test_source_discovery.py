"""Tests for the Source Discovery agent (M5).

Hermetic: the query-planning model is a `FakeProvider` and the search backend is
a `FakeSearchProvider`, so no network is touched. The tests pin the agent's
contract: it plans queries via the model, retrieves sources via the tool, and
the resulting `Source`s carry tool-authored URLs + discovery provenance — the
model never mints a URL.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.source_discovery import (
    DiscoveryError,
    SourceDiscoveryAgent,
    _DiscoveryOutput,
    _DiscoveryQuery,
)
from app.schemas.research_state import ResearchPlan, SourceType, SubQuestion
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider


def _plan() -> ResearchPlan:
    return ResearchPlan(goal="g", sub_questions=[SubQuestion(text="q1"), SubQuestion(text="q2")])


def _agent(
    output: _DiscoveryOutput,
    search: FakeSearchProvider,
) -> tuple[SourceDiscoveryAgent, FakeProvider]:
    llm = FakeProvider([output])
    router = ModelRouter(
        providers={"fake": llm},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    return SourceDiscoveryAgent(router, search), llm


def test_discover_promotes_search_results_to_sources() -> None:
    output = _DiscoveryOutput(
        queries=[
            _DiscoveryQuery(query="effects of X", source_type=SourceType.WEB),
            _DiscoveryQuery(query="X papers", source_type=SourceType.PAPER),
        ]
    )
    search = FakeSearchProvider(
        by_query={
            "effects of X": [SearchResult(url="https://a.com", source_type=SourceType.WEB)],
            "X papers": [SearchResult(url="https://arxiv.org/1", source_type=SourceType.PAPER)],
        }
    )
    agent, _ = _agent(output, search)
    sources = asyncio.run(agent.discover(_plan()))
    assert {s.url for s in sources} == {"https://a.com", "https://arxiv.org/1"}


def test_sources_carry_discovery_provenance_not_llm_authored() -> None:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider([SearchResult(url="https://a.com", source_type=SourceType.WEB)])
    agent, _ = _agent(output, search)
    src = asyncio.run(agent.discover(_plan()))[0]
    # The URL came from the search tool; provenance records the discovery path.
    assert src.discovered_via == "search:fake"
    assert src.raw_metadata["query"] == "q"
    assert src.id.startswith("src_")  # schema-minted, not model-authored


def test_uses_planning_role_with_sub_questions_in_prompt() -> None:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider([SearchResult(url="https://a.com", source_type=SourceType.WEB)])
    agent, llm = _agent(output, search)
    asyncio.run(agent.discover(_plan()))
    assert llm.calls[0].model == "planning-model"
    assert "q1" in llm.calls[0].prompt and "q2" in llm.calls[0].prompt


def test_no_queries_raises_discovery_error() -> None:
    agent, _ = _agent(_DiscoveryOutput(queries=[]), FakeSearchProvider([]))
    with pytest.raises(DiscoveryError):
        asyncio.run(agent.discover(_plan()))


def test_search_returns_nothing_raises_discovery_error() -> None:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    agent, _ = _agent(output, FakeSearchProvider([]))  # search yields nothing
    with pytest.raises(DiscoveryError):
        asyncio.run(agent.discover(_plan()))
