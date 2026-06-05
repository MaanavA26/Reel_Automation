# ADR 0053: Generative-video visual adapters (Veo / Runway / Luma / Pika / Kling)

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Tech Lead, advisor (council tool unavailable; advisor served as the second opinion)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Media Production layer (CLAUDE.md §3.3) already has a *retrieval* visual seam:
`VisualProvider.search(query, limit)` (`media/visuals/base.py`, ADR 0019/0024)
maps a keyword to **already-existing** stock B-roll via a single synchronous
request (`StockVisualProvider` over Pexels). The operator wants the system to also
**generate** original footage with AI text-to-video models, behind one
provider-neutral seam, config-selectable across **all** the major vendors:
Google **Veo** (Vertex AI), **Runway** (Gen-3/Gen-4), Luma **Dream Machine**,
**Pika**, and **Kling**.

Generative video is a fundamentally different shape from both the chat-LLM fabric
and the stock-retrieval seam:

- **Generate, not retrieve.** The input is a *prompt*, the output is a *newly
  synthesized* clip — not a lookup of an existing asset. `search`'s
  keyword-to-stock contract does not fit; forcing it would conflate retrieval and
  generation and break the evidence/retrieval-vs-inference boundary the layer
  keeps structural (CLAUDE.md §11).
- **No universal API shape.** Unlike the chat-LLM seam (where many vendors speak a
  near-common OpenAI-compatible JSON shape), these five differ in request schema,
  **async submit + poll** lifecycle, auth, and output location (URL vs inline
  bytes). There is no single wire contract to standardize on.
- **Async job lifecycle.** Each is *submit a request -> get a job id -> poll until
  terminal -> read the finished asset URL*, which can take minutes.

Constraints match the existing live adapters: the sandbox has HTTP egress but no
generative-video vendor and no live keys, and `httpx` is already a runtime
dependency. So every adapter is built on `httpx` and is fully offline-verifiable
via `httpx.MockTransport`; **none is live-validated** (see the caveat below).

## Decision

### A new seam: `GenerativeVisualProvider`

Add a *new* protocol `app.media.visuals.generative.GenerativeVisualProvider` with
an async `generate(*, prompt, duration_ms=5000, aspect="9:16") -> VisualClip`,
**not** an extension of `VisualProvider`. It is a deterministic *tool* (CLAUDE.md
§3.3/§4), exactly like `StockVisualProvider`: the upstream Short-Form Content
Strategist decides *what* to depict; the adapter *executes* the generation and is
the only thing that mints a real asset `uri` (an LLM never authors one).

Two deliberate contract choices:

- **Same `VisualClip` out.** Generation returns the *same* descriptor the
  retrieval seam produces, so the composition step is unchanged. The adapter
  returns the vendor's **hosted result URL** as `uri` (these vendors deliver large
  finished assets as download URLs, never inline bytes), which the composition
  root's existing `_make_filesystem_visual_sink` already fetches to a local
  `file://` uri for ffmpeg. No bytes-sink (the NVIDIA-TTS pattern) is introduced —
  it is unnecessary here and would change the composition seam. Provenance is
  `produced_via = "genvideo:<vendor>"` (the `genvideo:` prefix distinguishes a
  synthesized clip from a retrieved `"visuals:stock"` one).
- **Dimensions from the *request*, not the response.** The inverse of
  `StockVisualProvider` (which reads dims from Pexels): a generative adapter
  *requests* a resolution from the chosen aspect and sets `width`/`height` from
  it (the poll response rarely carries dimensions). `_dims_for_aspect` is the
  shared pure mapping; an unknown aspect fails loud (no silent default).

### Per-vendor adapters over one polling template-method base

There is no universal shape, so each vendor gets its **own adapter** to its
**documented** contract. But all five share the *same lifecycle*, so the loop is
written once: `_PollingGenerativeProvider` (a template-method base, the scaled-up
analogue of the pure/impure split in `nvidia.py`) owns `generate` — submit, then
poll with `raise_for_status` per call, bounded by a max-attempts × interval
**wall-clock budget** — plus the error boundary. Each adapter implements only the
per-vendor *wire-shape hooks*:

- `_auth_headers()` — **deliberately a hook, not unified in the base.** The
  vendors' auth diverges sharply: Veo uses a GCP access token + project/region in
  the URL; Kling mints a short-lived HS256 JWT from key+secret; Runway/Luma are
  static bearer keys; Pika (via fal) uses `Authorization: Key`. Baking one bearer
  header into the base would force a refactor for Veo and Kling.
- `_build_submit` / `_parse_submit` / `_build_poll` / `_poll_body` / `_parse_poll`
  — each pure and isolated, mapping the vendor's status vocabulary onto a
  provider-neutral `JobState` (pending/done/failed).

Two **distinct timeouts**, not conflated: the per-request `httpx` timeout is small
(each poll is a fast status check); the wall-clock poll budget is *minutes*
(generation is slow). `sleep` is injected (`asyncio.sleep` default) so hermetic
tests run instantly and the timeout path is testable.

Adapters live in a `media/visuals/generative_providers/` subpackage (one module
per vendor) — chosen over flat files because five same-shaped adapters read more
clearly grouped, and it keeps the seam + base + fake in `generative.py` distinct
from the concrete wire shapes.

Per-vendor notes:

- **Runway** — `POST /v1/text_to_video` (`X-Runway-Version` header + bearer),
  pixel-pair `ratio`, poll `GET /v1/tasks/{id}`, `status` SUCCEEDED/FAILED,
  `output[0]`.
- **Luma** — `POST /dream-machine/v1/generations` (bearer), native aspect strings,
  poll `GET /dream-machine/v1/generations/{id}`, `state` completed/failed,
  `assets.video`. The cleanest of the five.
- **Veo (Vertex AI)** — `:predictLongRunning` (GCP access token, project/region in
  the URL), poll via **POST** `:fetchPredictOperation` with `{operationName}`,
  `done`/`error`, result `response.videos[].gcsUri`. Requires a `storage_uri`
  (`gs://`) so Veo writes to GCS and returns a fetchable URI rather than inline
  base64.
- **Kling** — `POST /v1/videos/text2video`, auth is an **HS256 JWT** minted per
  request from `ak`/`sk` (`iss`/`exp`/`nbf` claims), signed with the Python
  **stdlib** (`hmac`+`hashlib`+base64url — no new dependency, the ADR 0047
  posture), poll `GET .../{task_id}`, `data.task_status` succeed/failed,
  `data.task_result.videos[0].url`.
- **Pika** — has **no stable first-party public REST API**; the documented
  self-serve path is via **fal.ai**'s generic async *queue* API
  (`fal-ai/pika/v2.2/text-to-video`). The adapter speaks the fal queue contract
  (submit returns `status_url`/`response_url`; the result lives at a *separate*
  URL), so `generate` is overridden to thread those URLs.

### Config-driven selector (single pick, NOT a fallback router)

`build_generative_visual_provider(settings)` (`media/visuals/generative_router.py`)
reads `Settings`, builds the **one** configured adapter, and passes the vendor's
secrets in at construction (the adapters never read global `Settings`; this factory
is the only seam that does — mirroring `_build_search_provider`). It is
**deliberately not** a `TTSRouter`-style ordered-fallback router: TTS falls back
across cheap/local backends because a render must produce *some* audio;
generative video is paid and minutes-long per call, so silently retrying a
*different* vendor on failure multiplies cost + latency for no benefit. Selection
by `generative_video_backend`: empty (default) → `None` (feature off, the existing
retrieval/ffmpeg path unaffected — mirroring how the stock provider is `None`
without a key); a known name with creds → that adapter; unknown name or missing
creds → a loud `GenerativeRoutingError`.

### Agent-vs-tool placement

These adapters are deterministic **tools** (CLAUDE.md §4), like every other media
adapter: the *judgment* — whether to generate vs retrieve, and what to depict —
belongs to the upstream strategist/orchestrator; the adapter only executes the
documented call and maps the result. No agent logic lives here.

### Scope discipline (strictly additive to shared files)

`config.py` and `composition.py` edits are **append-only** (other PRs land on
these files): new `Settings` fields appended after the TTS block; a new import,
a new optional `MediaDeps.generative_visuals` field (default `None`, appended
last), and an appended populate-line + closable in `build_media_deps`. The
existing `VisualProvider`/`StockVisualProvider` behavior is untouched. No new
runtime dependency (`httpx`/Pydantic are runtime; Kling's JWT uses the stdlib).

## Not live-validated (the explicit caveat + follow-up)

**None of the five adapters has been validated against a live endpoint** — this
offline sandbox has no generative-video vendor and no keys. Each is built to the
vendor's *documented* contract (confirmed against official docs / the vendor's
own OpenAPI spec where available, e.g. Luma's), and each adapter docstring +
this ADR carry the same last-mile caveat the repo already carries for NVIDIA-TTS
(ADR 0047) and YouTube-upload (ADR 0033). The wire shape is isolated in the hook
methods so the first live call can confirm/adjust it with a *small edit, not a
rewrite*. **Follow-up:** validate each adapter against a real key (a
`@pytest.mark.integration` smoke per vendor), highest-risk first.

**Highest-risk on the documented-vs-real contract:**

1. **Veo** — most divergent: the Vertex AI LRO shape, GCP token refresh, and the
   `storageUri`-to-get-a-URL requirement; a second Gemini-API surface
   (`generativelanguage`, API-key auth) also exists and may be the right target
   for some setups.
2. **Pika** — no first-party API; correctness depends on fal's per-model input
   schema (fal's *queue envelope* is stable, but the Pika input fields may differ
   by model version).
3. **Kling** — JWT auth detail (claim names, TTL, the regional base host) and the
   `data`-envelope shape.

Runway is medium-risk (the dated version header + ratio enum); Luma is lowest
(its official OpenAPI spec pins the contract).

## Alternatives considered

- **Extend `VisualProvider.search` for generation.** Rejected: conflates retrieve
  and generate, and the call args (prompt vs keyword) and lifecycle (async job vs
  one request) do not fit.
- **One unified adapter via a config-driven request template.** Rejected: there
  is no universal shape — auth, lifecycle, and result location all differ; a
  template would become a tangle of per-vendor branches.
- **Unify auth in the base.** Rejected: Veo (GCP token + URL) and Kling (JWT)
  break the single-bearer assumption; `_auth_headers` is a hook.
- **A fallback router across vendors (like `TTSRouter`).** Rejected: paid,
  minutes-long generation makes silent cross-vendor fallback the wrong default.
- **Return bytes via an injected sink (the NVIDIA-TTS pattern).** Rejected: these
  vendors return URLs; returning the URL keeps composition unchanged.
- **Add PyJWT for Kling.** Rejected: HS256 is a few stdlib lines; the repo's
  no-new-dep posture (ADR 0047) holds.
- **Fully wire a generated clip into the render path now.** Deferred:
  capability-before-wiring (the ADR 0047 precedent). The factory + config fields +
  an optional `MediaDeps.generative_visuals` are shipped; consuming it in the
  render path is the documented follow-up.

## Consequences

- One provider-neutral generative-video seam + five concrete adapters + a
  config selector, all fully hermetic-testable, behind a new protocol that leaves
  the retrieval seam and the composition step unchanged.
- The async job-poll lifecycle is written once and shared; per-vendor divergence
  is isolated to small pure hooks, so a wire-shape correction after the first live
  call is a one-method edit.
- No production render wiring yet (capability-before-wiring); the `MediaDeps`
  field is built + closed but not consumed by the render path — a documented
  follow-up alongside per-vendor live validation.
