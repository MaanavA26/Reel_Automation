"""Live `TrendProvider` adapter over a generic trends/keyword REST source.

A single concrete `TrendProvider` built on ``httpx`` (a runtime dependency), so
request building and the response → `TopicIdea` mapping are unit-testable
**offline** via ``httpx.MockTransport``; only the live call needs network (a
``@pytest.mark.integration`` smoke test).

It targets a deliberately generic wire shape so it can sit in front of any
trends/keyword API behind the provider-neutral protocol (CLAUDE.md §6): a single
``GET /trends`` taking ``{q, limit}`` and an API key header, returning
``{"trends": [{"keyword", "score", "url"?, "title"?}]}``. A concrete vendor
adapter (Google Trends / a keyword-research SaaS) is a config/wiring follow-up,
not new code here.

Per CLAUDE.md §4 this is a deterministic *tool*: a future strategy agent decides
*which* niche to mine; this adapter *executes* the discovery and is the only
thing that mints a real ``sourced_via``/``url``, keeping the evidence-vs-inference
boundary (CLAUDE.md §11) structural — an LLM can never author a topic's
provenance.

Error boundary (mirrors the search/LLM adapters, ADR 0013/0007): *operational*
failures (429 rate-limit, timeout, 5xx) are surfaced as raised ``httpx`` errors
for the caller to handle (retries/budgets live at the orchestration layer) — they
are not swallowed here. Only an unparseable response *shape* is wrapped in
`TrendError`. A response with no trends is a valid empty result, not an error.
The API key is never placed in an error message or repr.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.lifecycle import CloseOwnedClientMixin
from app.topics.base import TopicIdea

PROVIDER_NAME = "trends"
_DEFAULT_BASE_URL = "https://api.example-trends.com"
# Bound the upstream-body excerpt in error messages so a full provider response
# never lands in logs / surfaced errors (info-leak guard, ADR 0043/0044).
_ERR_BODY_MAX = 500


class TrendError(RuntimeError):
    """Raised when a trends response cannot be parsed into `TopicIdea`s.

    Transport/status failures (timeout, 429, 5xx) are *not* wrapped — they
    propagate as ``httpx`` errors for the orchestration layer to handle.
    Defined locally (mirroring `SearchError` in the search adapters) so this
    module is self-contained.
    """


class HttpTrendProvider(CloseOwnedClientMixin):
    """A `TrendProvider` over a generic trends/keyword REST API.

    Hardened like the search/LLM adapters: a bounded timeout and a key that never
    leaks into logs, reprs, or error messages. This calls a single trusted API
    endpoint (not attacker-influenced URLs), so SSRF caps are out of scope.
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
            raise TrendError("api_key is required (set REEL_AUTOMATION_TRENDS_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def discover(self, *, niche: str, limit: int = 10) -> list[TopicIdea]:
        """Run ``niche`` against the trends API and map hits to `TopicIdea`s.

        Returns at most ``limit`` ideas, mirroring `FakeTrendProvider`. A hit
        missing a usable title/keyword is skipped (it cannot become a topic).
        """
        response = await self._client.get(
            f"{self._base_url}/trends",
            headers={
                "X-Api-Key": self._api_key,
                "Accept": "application/json",
            },
            params={"q": niche, "limit": max(limit, 1)},
        )
        response.raise_for_status()
        data: Any = response.json()
        return _map_trends(data, niche=niche, limit=limit)


def _coerce_signal(value: Any) -> float | None:
    """Best-effort coerce a provider score to ``float``; ``None`` on absence/garbage."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map_trends(data: Any, *, niche: str, limit: int) -> list[TopicIdea]:
    """Map a generic ``/trends`` payload to `TopicIdea`s.

    An absent/empty ``trends`` list means "no trends for this niche" — a valid
    empty outcome (the repo's "thin result is valid" pattern), not an error.
    Only a *present but mistyped* payload is wrapped in `TrendError`.
    """
    if not isinstance(data, dict):
        raise TrendError(f"unexpected trends response shape: {repr(data)[:_ERR_BODY_MAX]}")

    raw = data.get("trends")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TrendError(f"unexpected trends 'trends' shape: {raw!r}")

    ideas: list[TopicIdea] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        keyword = item.get("keyword")
        title = item.get("title") or keyword
        if not title:
            continue  # no title/keyword -> cannot become a topic; skip
        url = item.get("url")
        ideas.append(
            TopicIdea(
                title=str(title),
                sourced_via=f"trends:{PROVIDER_NAME}",
                niche=niche,
                keyword=str(keyword) if keyword else None,
                signal=_coerce_signal(item.get("score")),
                url=str(url) if url else None,
            )
        )
    return ideas[:limit]
