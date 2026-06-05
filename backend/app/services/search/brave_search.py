"""Live `SearchProvider` adapter over the Brave Search API (httpx-based).

A second concrete `SearchProvider` alongside the Tavily adapter (ADR 0013),
added for failover/robustness — CLAUDE.md §6's policy-driven, provider-neutral
stance applied to the search fabric: switching or fanning out across search
backends is configuration, not new agent code.

Brave's Web Search endpoint is a single ``GET /res/v1/web/search`` authenticated
with an ``X-Subscription-Token`` header (a different wire shape from Tavily's
``POST`` + ``Authorization: Bearer``; each adapter speaks its own API behind the
shared `SearchProvider` protocol). It returns ranked web results whose
``description`` field carries the snippet that maps onto `SearchResult`.

Per CLAUDE.md §4 this is a deterministic *tool*: the search agent decides *what*
to search for; this adapter *executes* the search and is the only thing that
mints a real ``url``, keeping the evidence-vs-inference boundary (CLAUDE.md §11;
ADR 0006) structural — an LLM can never author a `Source.url`.

Built on ``httpx`` (a runtime dependency) so request building and the
response → `SearchResult` mapping are unit-testable **offline** via
``httpx.MockTransport``; only the live call needs network (a
``@pytest.mark.integration`` smoke test). See ADR 0021.

Error boundary (mirrors ADR 0007 / ADR 0013): *operational* failures (429
rate-limit, timeout) are surfaced as raised ``httpx`` errors for the Orchestrator
to handle (retries/budgets are deferred to that layer) — they are not swallowed
here. Only an unparseable response *shape* is wrapped in `SearchError`. A
response with no web results is a valid empty result, not an error. The API key
is never placed in an error message or repr.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.schemas.research_state import SourceType
from app.services.search.base import SearchResult

PROVIDER_NAME = "brave"
_DEFAULT_BASE_URL = "https://api.search.brave.com"
# Brave rejects ``count`` above 20 (HTTP 422); the protocol's ``limit`` is
# caller-controlled, so it must be clamped before it reaches the wire.
_MAX_COUNT = 20


class SearchError(RuntimeError):
    """Raised when a search response cannot be parsed into `SearchResult`s.

    Transport/status failures (timeout, 429, 5xx) are *not* wrapped — they
    propagate as ``httpx`` errors for the Orchestrator to handle (ADR 0007).
    Defined locally (mirroring `OpenAICompatError` in the LLM adapter) rather
    than in `base.py`, so this file is the whole diff and stays merge-clean with
    the sibling Tavily adapter, which defines its own.
    """


class BraveSearchProvider:
    """A `SearchProvider` over the Brave Web Search API.

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
            raise SearchError("api_key is required (set REEL_AUTOMATION_BRAVE_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def search(self, *, query: str, limit: int = 10) -> list[SearchResult]:
        """Run ``query`` against Brave and map hits to `SearchResult`s.

        Returns at most ``limit`` results, mirroring `FakeSearchProvider`. A
        result missing a ``url`` is skipped (it cannot become a `Source`).
        """
        response = await self._client.get(
            f"{self._base_url}/res/v1/web/search",
            headers={
                "X-Subscription-Token": self._api_key,
                "Accept": "application/json",
            },
            params={"q": query, "count": min(max(limit, 1), _MAX_COUNT)},
        )
        response.raise_for_status()
        data: Any = response.json()
        return _map_results(data, limit=limit)


def _map_results(data: Any, *, limit: int) -> list[SearchResult]:
    """Map a Brave ``/web/search`` payload to `SearchResult`s (web pages only).

    Brave nests web hits under ``web.results``. An absent ``web`` block or an
    absent ``results`` list means "no web results for this query" — a valid
    empty outcome (the repo's "thin result is valid" pattern), not an error.
    Only a *present but mistyped* payload is wrapped in `SearchError`.
    """
    if not isinstance(data, dict):
        raise SearchError(f"unexpected Brave response shape: {data!r}")

    web = data.get("web")
    if web is None:
        return []
    if not isinstance(web, dict):
        raise SearchError(f"unexpected Brave 'web' shape: {web!r}")

    raw_results = web.get("results")
    if raw_results is None:
        return []
    if not isinstance(raw_results, list):
        raise SearchError(f"unexpected Brave 'web.results' shape: {raw_results!r}")

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
                snippet=item.get("description"),
            )
        )
    return results[:limit]
