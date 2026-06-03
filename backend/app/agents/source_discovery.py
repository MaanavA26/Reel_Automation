"""Source Discovery agent — turns a research plan into discovered sources.

The agent is genuine *judgment* (CLAUDE.md §4): given the plan's sub-questions,
it decides what to search for — the queries and the source *types* most likely
to surface grounded evidence. It then delegates the actual retrieval to an
injected `SearchProvider` *tool* (the "agent uses a tool" pattern) and promotes
each tool result into a canonical `Source`.

The split is the §11 evidence-vs-inference boundary made structural: the LLM
authors only *queries* (an intent, not a claim); the search provider — never the
LLM — produces the `url`/`title` that become a `Source`. See ADR 0006.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.research_state import ResearchPlan, Source, SourceType
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter
from app.services.search.base import SearchProvider


class DiscoveryError(RuntimeError):
    """Raised when discovery cannot produce any sources."""


SYSTEM_PROMPT = (
    "You are a source-discovery strategist for a research engine. Given a "
    "research goal and its prioritized sub-questions, produce effective web "
    "search queries that would surface high-quality, groundable sources. For "
    "each query, choose the source type most likely to hold the answer "
    "(web, pdf, paper, youtube, repo, file). Prefer specific, well-targeted "
    "queries over broad ones; cover the sub-questions in priority order. Do not "
    "answer the questions and do not invent URLs — only propose search queries."
)


class _DiscoveryQuery(BaseModel):
    """One model-proposed search query (model-output DTO; no ids/urls)."""

    query: str
    source_type: SourceType
    rationale: str | None = None


class _DiscoveryOutput(BaseModel):
    """Structured output of the discovery model call.

    The model authors queries only. URLs/ids/timestamps are never model-authored
    — they are minted by the search provider and the `Source` schema.
    """

    queries: list[_DiscoveryQuery] = Field(default_factory=list)


class SourceDiscoveryAgent:
    """Plans search queries (via the model) and retrieves sources (via a tool)."""

    def __init__(self, router: ModelRouter, search_provider: SearchProvider) -> None:
        self._router = router
        self._search = search_provider

    async def discover(self, plan: ResearchPlan, *, per_query_limit: int = 5) -> list[Source]:
        """Discover sources for ``plan``.

        Plans queries with the ``PLANNING``-role model, then runs each query
        through the search provider (sequentially, for deterministic offline
        replay), promoting results to `Source`s with discovery provenance.

        Raises `DiscoveryError` if no query yields any source — so the band
        never advances on empty acquisition (mirrors the planner's contract).
        """
        plan_output = await self._plan_queries(plan)
        if not plan_output.queries:
            raise DiscoveryError("discovery model proposed no search queries")

        sources: list[Source] = []
        for dq in plan_output.queries:
            results = await self._search.search(query=dq.query, limit=per_query_limit)
            for result in results:
                sources.append(
                    Source(
                        url=result.url,
                        type=result.source_type,
                        title=result.title,
                        discovered_via=f"search:{self._search.name}",
                        raw_metadata={"query": dq.query},
                    )
                )

        if not sources:
            raise DiscoveryError("search returned no sources for any query")
        return sources

    async def _plan_queries(self, plan: ResearchPlan) -> _DiscoveryOutput:
        model = self._router.for_role(ModelRole.PLANNING)
        return await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(plan),
            schema=_DiscoveryOutput,
        )

    @staticmethod
    def _build_prompt(plan: ResearchPlan) -> str:
        goal = plan.goal or "(no refined goal provided)"
        lines = "\n".join(f"- {sq.text}" for sq in plan.sub_questions)
        return (
            f"Research goal:\n{goal}\n\n"
            f"Sub-questions (priority order):\n{lines}\n\n"
            "Propose search queries."
        )
