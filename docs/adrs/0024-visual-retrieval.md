# ADR 0024: Visual / B-roll Retrieval Seam — Provider-Neutral Adapter

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

CLAUDE.md §3.3 lists "image/video generation or retrieval" as a Media Production
Layer responsibility, and ADR 0019 explicitly **deferred** the visual seam ("not
yet seamed; added when a composition consumer needs them"). That consumer now
exists: `CompositionService.render(..., visual_uris=...)` (ADR 0019) already
names "B-roll" as the ordered background assets it lays under narration and
captions, but nothing yet *produces* those uris. The repo owner's feature
backlog also calls out short B-roll clips synced to script as a target.

This ADR introduces the **retrieval** half of that responsibility — the seam that
turns a script-derived query into real, playable visual assets — mirroring the
twice-blessed fabric pattern the LLM (ADR 0003/0007), search (ADR 0006/0013/0021)
and ingestion (ADR 0008) bands already use: a provider-neutral Protocol + typed
artifact DTO + hermetic fake + one concrete httpx adapter.

## Decision

**Introduce `backend/app/media/visuals/` — a `VisualProvider` Protocol, a
`VisualClip` DTO, a hermetic `FakeVisualProvider`, and a real httpx-based
`StockVisualProvider` — as a self-contained band mirroring the search fabric.**

### 1. The band is a *tool*, never an agent (CLAUDE.md §4)

§4 lists "API wrappers" and retrieval as tool work. The upstream Short-Form
Content Strategist (the §5.6 agent) decides *what* the B-roll should depict; this
band *executes* the retrieval. So it has no `ModelProvider`/router dependency.
The provider — never an LLM — is the only thing that mints a real asset `uri`,
keeping the retrieval-vs-inference boundary (CLAUDE.md §11) structural, exactly as
the search fabric does for `Source.url`.

### 2. `VisualClip` DTO lives in `visuals/base.py`, not `app/media/schemas.py`

The band is self-contained, mirroring the search fabric where `SearchResult`
sits in `search/base.py` beside its protocol. `_gen_id` is a small **local copy**
(not a cross-layer import of a private `_` symbol) — the same copy-not-import
move ADR 0019 blessed for this layer. `VisualClip` is strict (`extra='forbid'`),
id-prefixed (`vis_`), and carries a **required `produced_via`** provenance string
(`"visuals:fake"` / `"visuals:stock"`), symmetric with `SynthesizedSpeech.produced_via`
/ `Source.discovered_via` (ADR 0006/0019).

Fields are minimal and load-bearing for the B-roll-synced-to-script use case:
`uri`, `kind` (`VisualKind.VIDEO`/`IMAGE`), `width`/`height` (`>0`), an optional
`duration_ms` (`None` for a still — only a clip has a timeline), and an optional
`attribution` (stock licenses often require an author credit). No speculative
fields (§7).

### 3. Real adapter: one documented wire contract (Pexels Videos API)

"Generic stock-media REST API" means **one** documented contract behind the
protocol, the way the Tavily and Brave search adapters each speak their own API.
`StockVisualProvider` speaks Pexels' `GET /videos/search` (the canonical free
vertical-B-roll source; `Authorization`-header auth), maps `videos[]` →
`VisualClip` (seconds→ms; portrait orientation requested for vertical short-form;
file-rendition dimension fallback), and is hardened **point-for-point** like the
Brave adapter (ADR 0021): empty key raises, key never leaks into repr/errors,
injectable `client` + bounded `timeout`, `per_page` clamped to Pexels' max of 80.

### 4. Key at construction, not `Settings`

The key is a constructor argument, keeping the seam config-root-agnostic (no
`config.py` touch, per scope). The integration test reads
`REEL_AUTOMATION_STOCK_API_KEY` from the environment directly. A `.env.example`
block and a composition-root wiring are deliberately out of scope.

### 5. Error boundary (mirrors ADR 0013/0021)

Operational failures (429/timeout/5xx via `raise_for_status`) propagate as
`httpx` errors for the caller/Orchestrator; only a malformed response *shape* is
wrapped in a locally-defined `VisualError`; an empty result set is a valid empty
outcome, not an error; a hit lacking a usable file link or dimensions is skipped.

## Consequences

### Positive

- The composition step's `visual_uris` input now has a real producer; the
  B-roll-synced-to-script feature is unblocked behind a swappable interface.
- Reuses the exact fabric pattern (Protocol + fake + httpx adapter + MockTransport
  tests + integration smoke test) — twice-blessed, low surprise, showcaseable.
- `produced_via` extends provenance to retrieved visuals end-to-end.

### Negative

- A second `_gen_id` copy now exists in the band (already-accepted ADR 0019 cost).
- One concrete backend (Pexels) for now; a second (e.g. Pixabay) or an image
  endpoint are drop-in follow-ups behind the protocol.

### Neutral

- `RecordedVisualSearch` call-capture mirrors `FakeSearchProvider.RecordedSearch`.
- No new dependency (`httpx` already a runtime dep); no schema/config change.

## Deferred (with reasons)

- **Composition-root wiring** of `StockVisualProvider` (and a `Settings` key +
  `.env.example` block) — out of this seam-only scope; lands when a media
  orchestrator consumes the band, the way the search adapter shipped wiring-free.
- **A second stock backend / Pexels image endpoint / Veo generation** — added on
  demand behind the protocol, to avoid speculative surface (§7).
- **Query authorship from the script** (the strategist → query mapping) — belongs
  to the deferred Deep Research → Media handoff contract (ADR 0019), not here.

## Alternatives considered

### Option A — Put `VisualClip` in `app/media/schemas.py`

**Pros:** every other media artifact DTO lives there; `_gen_id` is in scope.
**Cons:** the SCOPE is the new `visuals/` package only (schemas.py is off-limits),
and the search fabric — the pattern to mirror — keeps `SearchResult` beside its
protocol. **Rejected** in favor of a self-contained band.

### Option B — Model B-roll selection as an agent

**Pros:** superficially uniform with the research layer. **Cons:** retrieval is
deterministic execution (§4); the judgment ("what should the B-roll show") is an
upstream strategist concern. **Rejected** (the §11 "every step an agent" anti-pattern).

### Option C — A generic multi-backend client now

**Pros:** failover like the Tavily+Brave pair. **Cons:** one documented contract
is the established adapter shape; a second backend is a cheap follow-up. **Rejected**
as premature (§7).

## References

- [CLAUDE.md](../../CLAUDE.md) §3.3 (image/video retrieval), §4 (agent-vs-tool),
  §6 (provider abstraction), §7 (no overbuild), §11 (provenance; retrieval-vs-inference).
- [ADR 0019](0019-media-production-layer.md) (media layer seams; deferred this band;
  blessed the `_gen_id` copy and the `produced_via` provenance convention).
- [ADR 0006](0006-source-discovery-and-search-fabric.md) / [ADR 0021](0021-brave-search-adapter.md)
  (the Protocol + hermetic fake + hardened httpx adapter pattern mirrored here).
- [`docs/ROADMAP.md`](../ROADMAP.md) — Media Production Layer section.
