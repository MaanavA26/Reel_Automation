"""Live `SearchProvider` adapter over the Tavily Search API (httpx-based).

Tavily is purpose-built for LLM/research agents: a single ``POST /search`` with a
JSON body and ``Authorization: Bearer`` auth (the same shape as the M-LP.1
OpenAI-compatible LLM adapter — chosen over Brave's GET + ``X-Subscription-Token``
precisely so the two adapters mirror each other), and it returns ranked web
results carrying a content ``snippet`` that maps cleanly onto ``SearchResult``.

Per CLAUDE.md §4 this is a deterministic *tool*: the search agent decides *what*
to search for; this adapter *executes* the search and is the only thing that
mints a real ``url``, keeping the evidence-vs-inference boundary (CLAUDE.md §11;
ADR 0006) structural — an LLM can never author a `Source.url`.

Built on ``httpx`` (a runtime dependency) so request building and the
response → `SearchResult` mapping are unit-testable **offline** via
``httpx.MockTransport``; only the live call needs network (a
``@pytest.mark.integration`` smoke test). See ADR 0013.

Error boundary (mirrors ADR 0007): *operational* failures (429 rate-limit,
timeout) are surfaced as raised ``httpx`` errors for the Orchestrator to handle
(retries/budgets are deferred to that layer) — they are not swallowed here. Only
an unparseable response *shape* is wrapped in `SearchError`. The API key is
never placed in an error message or repr.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.schemas.research_state import SourceType
from app.services.search.base import SearchResult

PROVIDER_NAME = "tavily"
_DEFAULT_BASE_URL = "https://api.tavily.com"
# Bound the upstream-body excerpt in error messages so a full provider response
# never lands in ``ResearchState.error`` / logs (info-leak guard, ADR 0043).
_ERR_BODY_MAX = 500


class SearchError(RuntimeError):
    """Raised when a search response cannot be parsed into `SearchResult`s.

    Transport/status failures (timeout, 429, 5xx) are *not* wrapped — they
    propagate as ``httpx`` errors for the Orchestrator to handle (ADR 0007).
    """


class TavilySearchProvider:
    """A `SearchProvider` over the Tavily Search API.

    Hardened like the M-LP.1 LLM adapter: a bounded timeout and a key that never
    leaks into logs, reprs, or error messages. SSRF caps (size/redirect/
    content-type) are intentionally omitted — unlike the ingestion fetcher this
    calls a single trusted API endpoint, not attacker-influenced URLs.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise SearchError("api_key is required (set REEL_AUTOMATION_SEARCH_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def search(self, *, query: str, limit: int = 10) -> list[SearchResult]:
        """Run ``query`` against Tavily and map hits to `SearchResult`s.

        Returns at most ``limit`` results, mirroring `FakeSearchProvider`. A
        result missing a ``url`` is skipped (it cannot become a `Source`).
        """
        response = await self._client.post(
            f"{self._base_url}/search",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
        )
        response.raise_for_status()
        data: Any = response.json()
        return _map_results(data, limit=limit)


def _map_results(data: Any, *, limit: int) -> list[SearchResult]:
    """Map a Tavily ``/search`` payload to `SearchResult`s (web pages only)."""
    try:
        raw_results = data["results"]
    except (KeyError, TypeError) as exc:
        raise SearchError(
            f"unexpected Tavily response shape: {repr(data)[:_ERR_BODY_MAX]}"
        ) from exc
    if not isinstance(raw_results, list):
        raise SearchError(f"unexpected Tavily 'results' shape: {repr(raw_results)[:_ERR_BODY_MAX]}")

    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue  # no url -> cannot become a Source; skip
        results.append(
            SearchResult(
                # A web-search API returns web pages; we do not infer
                # PDF/paper/youtube from the URL (speculative, unrequested).
                url=str(url),
                source_type=SourceType.WEB,
                title=item.get("title"),
                snippet=item.get("content"),
            )
        )
    return results[:limit]
