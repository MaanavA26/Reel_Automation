"""Live `AnalyticsProvider` adapter over the YouTube Analytics API (httpx-based).

The concrete analytics backend behind the seam in `base.py`, for the project's
primary platform (YouTube Shorts; CLAUDE.md §1). Mirrors the Brave search adapter
(ADR 0021): an httpx adapter whose request building and response → DTO mapping are
unit-testable **offline** via ``httpx.MockTransport``; only the live call needs
network (a ``@pytest.mark.integration`` smoke test).

## One call, not two

views/likes live in the YouTube **Data API** (``videos.list``) while
watch-time/retention live in the **Analytics API** — a tempting two-call
skeleton. Verified against the Analytics ``reports.query`` reference: a *single*
``GET https://youtubeanalytics.googleapis.com/v2/reports`` returns all of them
when asked for ``metrics=views,likes,estimatedMinutesWatched,averageViewPercentage``
filtered to one video. That single clean path is what this adapter uses.

The endpoint **requires** ``startDate``/``endDate`` (``YYYY-MM-DD``) and an
``ids`` selector (``channel==MINE`` for the authenticated channel). The response
is column-oriented (``columnHeaders`` + ``rows``); this adapter maps **by column
name, not position**, so a reordering of the returned columns cannot silently
mis-assign a metric.

## Auth & hardening (mirrors Brave/TTS)

OAuth bearer token taken **at construction** (not from `Settings`, keeping the
seam config-root-agnostic), injectable client + bounded timeout, and a token that
never leaks into logs, reprs, or error messages. Error boundary mirrors Brave:
operational failures (401/403/429/timeout/5xx) propagate as raised ``httpx``
errors for the caller to handle; only a malformed response *shape* is wrapped in
`AnalyticsError`. A post id the channel has no rows for is **not found**, raised
as `AnalyticsError` (the seam forbids a fabricated zeroed snapshot).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.analytics.base import AnalyticsError, VideoStats
from app.core.lifecycle import CloseOwnedClientMixin

PROVIDER_NAME = "youtube"
_DEFAULT_BASE_URL = "https://youtubeanalytics.googleapis.com"
# Bound the upstream-body excerpt in error messages so a full provider response
# never lands in logs / surfaced errors (info-leak guard, ADR 0043/0044).
_ERR_BODY_MAX = 500
# The Analytics API requires a bounded date range. Lifetime-to-date is the
# natural default for "how has this video performed"; 2005-02-14 is YouTube's
# launch date, an unambiguous floor that no real upload predates.
_DEFAULT_START_DATE = "2005-02-14"
# Metric names verified against the reports.query reference.
_METRICS = "views,likes,estimatedMinutesWatched,averageViewPercentage"


class YouTubeAnalyticsProvider(CloseOwnedClientMixin):
    """An `AnalyticsProvider` over the YouTube Analytics ``reports.query`` API.

    The token is an OAuth access token for the channel whose videos are queried;
    obtaining/refreshing it is the caller's concern (an OAuth flow lives outside
    this deterministic adapter, mirroring how the search adapters take a ready
    API key).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_token: str,
        ids: str = "channel==MINE",
        base_url: str = _DEFAULT_BASE_URL,
        start_date: str = _DEFAULT_START_DATE,
        end_date: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not access_token:
            raise AnalyticsError("access_token is required (an OAuth bearer token)")
        self._access_token = access_token
        self._ids = ids
        self._base_url = base_url.rstrip("/")
        self._start_date = start_date
        self._end_date = end_date
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def fetch_stats(self, *, post_id: str) -> VideoStats:
        """Fetch lifetime stats for one video id and map them to `VideoStats`.

        ``post_id`` is a YouTube video id. ``endDate`` defaults to today (UTC) when
        not pinned at construction — computed per call so a long-lived provider
        keeps reporting up to the current day rather than a frozen date.
        """
        params = {
            "ids": self._ids,
            "metrics": _METRICS,
            "filters": f"video=={post_id}",
            "startDate": self._start_date,
            "endDate": self._end_date or _today_utc(),
        }
        response = await self._client.get(
            f"{self._base_url}/v2/reports",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
            params=params,
        )
        response.raise_for_status()
        data: Any = response.json()
        return _map_report(data, post_id=post_id)


def _today_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _map_report(data: Any, *, post_id: str) -> VideoStats:
    """Map a ``reports.query`` payload to `VideoStats`, keyed by column **name**.

    The response is ``{"columnHeaders": [{"name": ...}], "rows": [[...]]}``. An
    empty/absent ``rows`` means the channel has no data for this video — treated
    as **not found** (`AnalyticsError`), never a fabricated zeroed snapshot. A
    present-but-mistyped payload is wrapped in `AnalyticsError`.
    """
    if not isinstance(data, dict):
        raise AnalyticsError(
            f"unexpected YouTube Analytics response shape: {repr(data)[:_ERR_BODY_MAX]}"
        )

    headers = data.get("columnHeaders")
    if not isinstance(headers, list):
        raise AnalyticsError(f"unexpected 'columnHeaders' shape: {headers!r}")
    names = [h.get("name") for h in headers if isinstance(h, dict)]

    rows = data.get("rows")
    if rows is None or (isinstance(rows, list) and not rows):
        raise AnalyticsError(f"no analytics rows for post_id={post_id!r} (not found)")
    if not isinstance(rows, list) or not isinstance(rows[0], list):
        raise AnalyticsError(f"unexpected 'rows' shape: {rows!r}")

    row = rows[0]
    if len(row) != len(names):
        raise AnalyticsError("columnHeaders/row length mismatch in YouTube Analytics response")
    by_name = dict(zip(names, row, strict=True))

    try:
        return VideoStats(
            post_id=post_id,
            views=int(_require(by_name, "views")),
            likes=int(_require(by_name, "likes")),
            estimated_minutes_watched=_opt_float(by_name.get("estimatedMinutesWatched")),
            average_view_percentage=_opt_float(by_name.get("averageViewPercentage")),
            fetched_via=f"analytics:{PROVIDER_NAME}",
        )
    except (TypeError, ValueError) as exc:
        raise AnalyticsError(f"unparseable metric value: {exc}") from exc


def _require(by_name: dict[Any, Any], key: str) -> Any:
    if key not in by_name:
        raise AnalyticsError(f"missing required metric column {key!r}")
    return by_name[key]


def _opt_float(value: Any) -> float | None:
    """Coerce an optional numeric metric to float; ``None`` stays ``None``."""
    if value is None:
        return None
    return float(value)
