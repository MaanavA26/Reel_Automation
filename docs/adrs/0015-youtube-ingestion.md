# ADR 0015: YouTube Transcript Ingestion

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (lib-choice / timestamp-handling / failure-mode
  architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0008 shipped the ingestion seam (`FetchProvider`/parser/chunker +
`IngestionService`) for **WEB sources only**, and explicitly **deferred** the
PDF / YouTube / repo / paper paths behind an "external-API/credential" gate,
each "a new parser behind the same `FetchProvider`/parser seam." This ADR opens
the **YouTube** path — a CLAUDE.md §5.3 target source type and a natural fit for
short-form content (a faceless Short is often a re-narration of a talk/video).

YouTube transcript extraction is **deterministic** (fetch captions, normalize,
chunk — no judgment), so per CLAUDE.md §4 ("transcript extraction" is listed
there verbatim) it is **tool/service** work, not an agent. The build sandbox has
HTTP/API egress but **cannot reach PyPI** (ROADMAP build-environment note), so a
fresh dependency cannot be installed here — the same M-LP gate that defers the
LLM/search provider adapters.

## Decision

**Add a YouTube transcript ingestion path behind a new `TranscriptProvider`
seam, fully unit-testable offline, with the real library lazy-imported and the
live path integration-marked.**

1. **`TranscriptProvider` protocol + `TranscriptSegment` DTO + `TranscriptError`**
   (`services/ingestion/transcript.py`), mirroring the M6 `FetchProvider` trio.
   Async `fetch(*, url) -> list[TranscriptSegment]` for symmetry with
   `FetchProvider`. Two pure helpers live here too: `extract_video_id` (URL →
   11-char id, all common shapes) and `normalize_transcript` (segments → one
   whitespace-collapsed string) — both no-I/O and the strongest hermetic tests,
   since the real provider cannot run offline.
2. **`FakeTranscriptProvider`** (`fake_transcript.py`) — scripted segments per
   URL, records calls, raises `TranscriptError` on an unmapped URL. In its **own
   module** (not the shared `fakes.py`) to avoid a merge conflict with the
   sibling PDF-parser branch editing the same directory.
3. **`YouTubeTranscriptProvider`** (`youtube_transcript_provider.py`) — the real
   adapter over [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api)
   1.x (`YouTubeTranscriptApi().fetch(video_id)` → `FetchedTranscript`). The
   library is **lazy-imported inside the method**, not at module top, so the
   package imports clean (and all hermetic tests stay green) without the
   optional dep. The library is synchronous, so the blocking call runs in
   `asyncio.to_thread` to honor the async contract.
4. **`IngestionService` gains an *optional* `transcript_provider` kwarg**
   (default `None`). A new `SourceType.YOUTUBE` branch transcribes +
   normalizes + chunks (via the **existing** `chunk_text`). The optionality is
   load-bearing for scope: `deep_research.py` (where the service is constructed,
   ADR 0009 `ResearchDeps`) is **out of scope**, so when no provider is injected
   a YouTube source **falls through to the existing skip-and-log path** rather
   than crashing — every existing `IngestionService(fetch)` call site and test
   stays green with zero edits outside this branch. `TranscriptError` joins
   `FetchError`/`ParseError` as a per-source skip; `IngestionError` still raises
   only on zero total chunks.
5. **`youtube-transcript-api>=1.0,<2.0` as an optional `[youtube]` extra** in
   `pyproject.toml`, plus a `tool.mypy.overrides` entry (`ignore_missing_imports`)
   for the un-installable module — the clean fix under `warn_unused_ignores`,
   not an inline ignore. Live smoke test is `@pytest.mark.integration` and also
   `importorskip`s the extra, so the default suite/CI never touch it.

### Council forks (resolved)

- **Lib + fallback.** `youtube-transcript-api` is the lightweight, credential-free
  choice (public timedtext endpoint; no Data API key, no `yt-dlp`). **No second
  in-code library fallback** — the `TranscriptProvider` seam *is* the swap point
  (a future provider can replace it without touching the service); a second lib
  now is YAGNI and out of scope.
- **Timestamp handling → discard in v1.** `Chunk` has no field for
  `start`/`duration` and `research_state.py` is out of scope. `TranscriptSegment`
  *captures* them (provenance fidelity, transient DTO) but `normalize_transcript`
  drops them. Symmetric with ADR 0008 deferring `Chunk.parsed_via`: do not invent
  schema for a not-yet-consumed signal.
- **No-transcript / disabled / age-restricted / IP-blocked → wrap + skip.** The
  library raises subclasses of `YouTubeTranscriptApiException`; the adapter
  catches that **base** class and wraps into one `TranscriptError`, which the
  service treats exactly like a fetch failure (per-source skip + log).

## Consequences

### Positive

- A second source type now flows through the pipeline end-to-end, validating the
  ADR 0008 "new parser behind the same seam" promise with **no schema change**
  and **no `deep_research.py` edit**.
- The path is fully verifiable offline (pure helpers + fake provider + service
  routing tests), with the real library exercised live behind an integration
  marker — the established M-LP shape.
- The pure `extract_video_id` / `normalize_transcript` helpers are reusable and
  trivially testable.

### Negative

- The real provider is **un-runnable in the sandbox** (no PyPI), so its body is
  covered only by the integration test (skipped here) — accepted, identical to
  the M-LP LLM/search adapters.
- `youtube-transcript-api` scrapes an unofficial endpoint; it can break on
  YouTube changes or get IP-blocked at scale. Acceptable for v1 (wiring, not
  hardening); the seam isolates the blast radius.

### Neutral

- One optional ctor kwarg on `IngestionService`; the construction site is
  unchanged. Wiring a real `TranscriptProvider` into `ResearchDeps`/the graph is
  a deliberate **follow-up** (keeps this branch off the out-of-scope files).

## Deferred (with the gate that keeps each shut)

- **Wiring `YouTubeTranscriptProvider` into the graph** → the construction site
  is `deep_research.py`/`ResearchDeps`, out of scope for this branch.
- **Timestamped chunks** → needs a `Chunk` schema field (`research_state.py`),
  out of scope; revisit when a downstream consumer (e.g. clip-aligned media)
  needs segment timing.
- **Language selection / translation, manual-vs-generated track preference** →
  product-policy upgrade, not a v1 wiring need (default English + library
  fallback).
- **PDF / repo / paper ingestion** → still ADR 0008's gate; sibling/later work.

## Alternatives considered

- **`yt-dlp` or the official YouTube Data API.** Rejected for v1: heavier
  (`yt-dlp`) or credential-gated (Data API) for what is a caption fetch;
  `youtube-transcript-api` is the minimal fit and sits behind a swappable seam.
- **Top-level import of the library.** Rejected: it is not installable in the
  sandbox, so a top-level import would break package import and all 148 tests.
- **Storing timestamps on `Chunk` now.** Rejected: no downstream consumer and an
  out-of-scope schema change — mirrors ADR 0008's `parsed_via` deferral.
- **Reusing `fakes.py` for the fake provider.** Rejected: a sibling branch edits
  that file; a separate module avoids a merge conflict.

## References
- Related: [ADR 0008](0008-source-ingestion-and-fetch-fabric.md) (the ingestion
  seam this extends; the YouTube deferral being lifted), [ADR 0006](0006-source-discovery-and-search-fabric.md)
  (provider-seam + Fake + integration-marked-live pattern), [ADR 0007](0007-openai-compatible-llm-adapter.md)
  (the M-LP optional-dep / lazy-import / integration-test shape), [ADR 0009](0009-evidence-extraction.md)
  (`ResearchDeps`, the out-of-scope construction site).
- [CLAUDE.md](../../CLAUDE.md) §4 ("transcript extraction" = tool), §5.3 (YouTube
  source type), §7/§9 (scope discipline), §11.
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP (provider adapters; this YouTube path).
