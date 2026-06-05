# ADR 0036: Analytics / feedback loop — platform stats seam + topic scorer

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

CLAUDE.md §3.4 names an "analytics and feedback loop" as a future layer: pull
platform performance (views, watch-time, retention, likes) for published videos
back into the system to steer *what gets made next*. This ADR introduces that
layer's first component — a new `backend/app/analytics/` package — with two
halves, kept distinct per the §4 agent-vs-tool rule:

1. an `AnalyticsProvider` **seam** that *fetches* per-video stats (deterministic
   tool/service work — API-wrapping), and
2. a `feedback` **scorer** that *ranks topics* by an explainable performance
   score to feed the topic queue (also deterministic — the *judgment* of which
   ranked topics to pursue is left to an upstream agent).

The seam deliberately mirrors the existing search fabric
(`app.services.search.base`) so the repo has one provider-seam pattern, not two.

## Decision

### 1. The seam: `AnalyticsProvider` Protocol + `VideoStats` DTO

A `@runtime_checkable` Protocol with `name: str` and an async
`fetch_stats(*, post_id: str) -> VideoStats`, plus a strict (`extra="forbid"`)
`VideoStats` DTO. Three seam decisions the bare search pattern does not settle:

- **Natural key, no synthetic id.** Unlike a `Source` (discovered, so id-minted),
  the platform already owns the identity — the `post_id` you queried with *is*
  the key. Adding a `vid_`-style id would be gold-plating and would force a
  cross-layer `_gen_id` copy; `VideoStats` needs neither.
- **Provenance carried.** `fetched_via` (`"analytics:fake"` / `"analytics:youtube"`)
  + `fetched_at` (UTC default), symmetric with `Source.discovered_via` /
  `VisualClip.produced_via`. Stats are tool-measured, never LLM-minted — the §11
  boundary the search fabric holds for `Source.url`.
- **Platform-pure DTO.** No `topic` field: the topic↔video link is *our* internal
  association, an input to the scorer, not a platform property. Keeping it off the
  DTO holds clean band separation.

Watch-time and retention are kept as distinct kinds, never collapsed:
`estimated_minutes_watched` is *absolute*; `average_view_percentage` is a *ratio*
(0–100). Both are optional `| None` (None = "platform did not report it",
meaningfully different from 0). A not-found post is an `AnalyticsError`, not a
fabricated zeroed snapshot.

### 2. `YouTubeAnalyticsProvider` — one call, column-name-keyed

Verified against the YouTube Analytics `reports.query` reference: a **single**
`GET /v2/reports` with
`metrics=views,likes,estimatedMinutesWatched,averageViewPercentage`,
`filters=video=={post_id}`, `ids=channel==MINE`, and the **required**
`startDate`/`endDate` returns all four metrics — avoiding a two-call
Data-API+Analytics-API skeleton. The column-oriented response
(`columnHeaders` + `rows`) is mapped **by column name, not position**, so a
reordering cannot silently mis-assign a metric. `endDate` defaults to today (UTC)
per call so a long-lived provider keeps reporting current.

Hardening mirrors the Brave adapter (ADR 0021): OAuth bearer token **at
construction** (not `Settings`, keeping the seam config-root-agnostic), injectable
`client` + bounded timeout, token never in repr/logs/errors. Error boundary
mirrors Brave: operational failures (401/403/429/timeout/5xx) propagate as raised
`httpx` errors; only a malformed response *shape* (and a no-rows not-found) is
wrapped in `AnalyticsError`. Empty/absent `rows` = not found.

### 3. `feedback.score_topics` — batch-independent, explainable scoring

Given `Mapping[str, Sequence[VideoStats]]` (pre-grouped by the caller), returns a
ranked `list[TopicScore]`. The load-bearing decision is **batch-independent
(absolute) scoring, not batch-relative.** A feedback loop re-runs over time; a
min-max normalization across the current batch would make the same topic score
differently run-to-run from its neighbors and be undefined for a single topic. So
every component is scale-free and needs no batch:

- `engagement_rate = total_likes / max(total_views, 1)` — ratio in [0, 1];
- `avg_retention = mean(reported average_view_percentage) / 100` — ratio in [0, 1];
- `views_saturation = total_views / (total_views + VIEWS_REFERENCE)` — a saturating
  transform of unbounded views into [0, 1), `VIEWS_REFERENCE` a documented constant.

The final `score` is a fixed, named-weight blend (`WEIGHT_VIEWS=0.4`,
`WEIGHT_RETENTION=0.4`, `WEIGHT_ENGAGEMENT=0.2`), so it is total, comparable across
runs, and in [0, 1]. **Explainability (§11) is structural:** `TopicScore` carries
the component breakdown + raw totals + `video_count` beside the number — the
breakdown is what "explainable" means here. Ranking is score-desc with a
deterministic **tie-break on topic ascending** (the eval-harness lexicographic
idiom). Edge cases: empty input → empty ranking; a topic with no stats → kept at
score 0.0 (distinct signal from "never tried"); zero views guarded; missing
retention excluded from the average (not counted as 0).

## Consequences

**Positive.** The fourth-layer feedback loop has a real, tested first component
with one provider-seam pattern shared with search. The scorer is a pure,
hermetic, explainable tool that closes the loop to the topic queue while leaving
the pursue/phrasing judgment to an agent (clean §4 split). The YouTube adapter is
offline-testable to the wire (MockTransport) with a live `@pytest.mark.integration`
smoke; the token stays out of `config.py`.

**Negative / deferred.** Only YouTube has a concrete adapter; Instagram/others are
new adapters behind the same Protocol when needed. No wiring: nothing *calls*
`fetch_stats` or `score_topics` yet (no orchestrator/scheduler, no topic-queue
store) — this PR ships the capability, adoption is a later wiring change (the M-LP
"capability only" pattern). The OAuth token lifecycle (obtain/refresh) is the
caller's concern, outside this deterministic adapter. The score weights and
`VIEWS_REFERENCE` are a v1 policy, tunable as channel data accrues.

**Risks.** None to existing code — this PR adds only the new `analytics/` package
and its tests; it touches no `config.py`/`main.py`/`pyproject.toml`/`api/` or any
agent/service/workflow logic.
