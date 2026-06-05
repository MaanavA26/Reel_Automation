# ADR 0033: Publishing / Social-Ops Layer — Provider-Neutral Upload Fabric

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

CLAUDE.md §3.4 lists "social media operations / publishing management" as a
future major layer, to be introduced via an ADR. With the Media Production layer
now producing a finished `RenderedVideo` artifact (ADR 0019), the pipeline has an
output that nothing yet *delivers* to a platform. This ADR introduces the
**fourth major component**: the band that uploads finished videos to short-form
platforms (YouTube Shorts, and — pending — Instagram Reels / TikTok).

It mirrors the twice-blessed fabric pattern the LLM (ADR 0003/0007), search
(ADR 0006/0013/0021), ingestion (ADR 0008) and visuals (ADR 0024) bands already
use: a provider-neutral Protocol + typed DTOs + a hermetic fake + one hardened
httpx adapter + protocol-conformant skeletons for the deferred backends.

## Decision

**Introduce `backend/app/publishing/` — a `PublishingProvider` Protocol, a
`PublishTarget` / `PublishResult` DTO pair, a hermetic `FakePublishingProvider`,
a real httpx-based `YouTubeShortsPublisher`, and protocol-conformant
`TikTokPublisher` / `InstagramReelsPublisher` skeletons.**

### 1. The band is a *tool*, never an agent (CLAUDE.md §4)

§4 lists "API wrappers", "file IO", and "storage access" as tool work. The
upstream agentic layer decides *what* to publish and *when*; this band *executes*
the upload. So it has no `ModelProvider` / router dependency. The provider —
never an LLM — is the only thing that mints a real platform post id/url, the
publishing-side analogue of the search fabric minting a real `Source.url`
(CLAUDE.md §11; the evidence/provenance boundary made structural).

### 2. DTOs live in `publishing/base.py`, not `app/media/schemas.py`

The band is self-contained, mirroring the search fabric (`SearchResult` in
`search/base.py`) and the visuals band — and `schemas.py` is off-limits anyway.
`_gen_id` is a small **local copy** (the ADR 0019-blessed copy-not-import
convention). `PublishResult` is strict (`extra='forbid'`), id-prefixed (`pub_`),
and carries a **required `published_via`** provenance string (`"publish:youtube"`
/ `"publish:fake"`), symmetric with `RenderedVideo.produced_via` /
`Source.discovered_via` (ADR 0006/0019). `PublishTarget` is minimal and
load-bearing only (CLAUDE.md §7): `title`, `description`, `tags`,
`privacy_status` — defaulting to `private` so an un-set target never
accidentally posts publicly.

### 3. Real adapter: YouTube Data API v3 resumable upload (one wire contract)

`YouTubeShortsPublisher` speaks the documented two-step resumable-upload
protocol (verified against Google's docs, not memory):

1. **Initiate** `POST {upload_base}/youtube/v3/videos?uploadType=resumable&part=snippet,status`
   with the video-resource metadata JSON body and `X-Upload-Content-Type` /
   `X-Upload-Content-Length` headers; the response carries the resumable session
   URI in the **`Location`** header.
2. **Upload** `PUT {session_uri}` with the raw video bytes; the success response
   is the created video resource, whose top-level **`id`** is the new video id.
   The watch url is minted as the canonical `watch?v={id}` form.

A single PUT of the full bytes is a valid resumable-upload completion; chunked /
resume-on-interruption upload is a documented deferral. Shorts signalling: the
`#Shorts` tag is appended to the description (the documented creator-side
convention) and a `Shorts` tag is added.

### 4. Storage seam: an injected `VideoSource` (the read-side `AudioSink`)

A `RenderedVideo` is a storage-owned descriptor (ADR 0019), so the adapter does
not choose where the bytes live — a `VideoSource = Callable[[str], bytes]`
resolves `video_uri` → bytes, injected at construction. This is the read-side
mirror of the TTS adapter's write-side `AudioSink`. Tests inject an in-memory
source; a real deployment injects an object-store / filesystem reader.

### 5. Token at construction, not `Settings`

The OAuth **access token** is a constructor argument (`config.py` untouched, per
scope; never leaked into repr/errors). Token **refresh** (refresh-token →
access-token exchange) is the caller's responsibility and a documented deferral.
The integration test reads `REEL_YOUTUBE_ACCESS_TOKEN` + `REEL_YOUTUBE_TEST_VIDEO`
from the environment.

### 6. Error boundary (mirrors ADR 0013/0021/0022)

Operational failures (401/403/429, timeouts, 5xx via `raise_for_status`)
propagate as native `httpx` errors for the orchestrator (retries/budgets live
there); only a contract-violating response *shape* — missing `Location` on
initiate, missing `id` on completion — is wrapped in a locally-defined
`PublishError`. The skeleton adapters raise the same `PublishError` with
`"adapter pending"`.

### 7. Coupling acceptance

`publishing/base.py` imports `RenderedVideo` from `app.media.schemas`. This is
the *intended consumption* of the published artifact (a read of a stable
contract), the single deliberate cross-layer import equivalent to the media
pipeline's `CreatorPacket` import — not a write to another band.

## Consequences

### Positive

- The Media layer's `RenderedVideo` now has a real delivery target behind a
  swappable interface; YouTube Shorts upload is unblocked.
- Reuses the exact fabric pattern (Protocol + fake + httpx adapter +
  MockTransport tests + integration smoke test) — twice-blessed, low surprise,
  showcaseable (CLAUDE.md §12).
- `published_via` extends provenance to the published artifact end-to-end.

### Negative

- A third `_gen_id` copy now exists (the already-accepted ADR 0019 cost).
- Only one concrete backend (YouTube) for now; TikTok / Instagram are explicit
  skeletons, drop-in follow-ups behind the protocol.

### Neutral

- No new dependency (`httpx` already a runtime dep); no `config.py` / `main.py` /
  `pyproject.toml` / schema change (adapter + DTOs only).
- The live smoke test is *side-effecting* (uploads a real video) — gated on both
  a real token and a real video path, always `private`, skipped otherwise.

## Deferred (with reasons)

- **TikTok / Instagram Reels concrete adapters** — skeletons today; each uses a
  distinct create-container → publish flow, added on demand behind the protocol
  (avoids speculative surface, §7).
- **OAuth token refresh** — the caller supplies a valid access token; the
  refresh-token exchange belongs to a credential/wiring layer, not this adapter.
- **Composition-root wiring + a `Settings` key** — out of this seam-only scope;
  lands when a publishing orchestrator consumes the band (the wiring-free shape
  the search/visuals adapters shipped in).
- **Chunked / resumable-on-interruption upload** — the single-PUT completion is
  a valid resumable upload; chunking is a follow-up if large files demand it.

### Known limitation

- The byte-upload `PUT` currently sends `Content-Type: video/*` (matching the
  initiate `X-Upload-Content-Type`). The wildcard is documented for the initiate
  header; if YouTube rejects it as the *actual* upload content-type, the fix is a
  concrete MIME (e.g. `video/mp4`). This is the one path the hermetic
  `MockTransport` test cannot exercise — it surfaces only via the live smoke test.

## Alternatives considered

### Option A — Use `google-api-python-client` instead of raw httpx

**Pros:** handles resumable-upload chunking and auth refresh. **Cons:** a heavy
new dependency, against the explicit scope (`httpx` only, already a dep), and it
would hide the wire contract that the rest of the repo's adapters keep explicit
and offline-testable via `MockTransport`. **Rejected.**

### Option B — Put DTOs in `app/media/schemas.py`

**Pros:** colocated with `RenderedVideo`. **Cons:** `schemas.py` is off-limits,
and the search/visuals fabric — the pattern to mirror — keeps DTOs beside their
protocol. **Rejected** in favor of a self-contained band.

### Option C — Model publishing as an agent

**Pros:** superficially uniform with the research layer. **Cons:** uploading is
deterministic execution (§4); the judgment ("what/when to publish") is an
upstream concern. **Rejected** (the §11 "every step an agent" anti-pattern).

## References

- [CLAUDE.md](../../CLAUDE.md) §3.4 (publishing layer via ADR), §4 (agent-vs-tool),
  §6 (provider abstraction), §7 (no overbuild), §11 (provenance boundary).
- [ADR 0019](0019-media-production-layer.md) (`RenderedVideo` artifact; the
  `_gen_id` copy + `produced_via` provenance convention; the `AudioSink` storage seam).
- [ADR 0021](0021-brave-search-adapter.md) / [ADR 0022](0022-tts-adapter.md) /
  [ADR 0024](0024-visual-retrieval.md) (the Protocol + hermetic fake + hardened
  httpx adapter + `MockTransport` pattern mirrored here).
- YouTube Data API v3 — `videos.insert` + resumable upload protocol.
- [`docs/ROADMAP.md`](../ROADMAP.md) — Publishing / Social-Ops Layer section.
