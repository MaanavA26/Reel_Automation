# ADR 0008: Source Ingestion and the Fetch Fabric

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** Tech Lead, Council (fetch-architecture / schema-provenance / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M6 opens the ingestion step of the Knowledge Acquisition band: turn the
`Source`s discovered in M5 into `Chunk`s of normalized text that the Evidence
Extraction agent (M7) can ground claims in. `Chunk` (id, source_id, text,
position) and the `KnowledgeAcquisitionState.chunks` channel already exist.

Ingestion is **deterministic** — fetch, parse, chunk, normalize — so per
CLAUDE.md §4 it is *tool/service* work, not an agent (no LLM). Unlike prior
network-touching milestones (M2 model fabric, M5 search), the build sandbox now
has **outbound network egress** (confirmed live: `example.com` → 200 HTML), which
changes the build-vs-defer calculus for the real fetcher.

## Decision

**Ship the fetch fabric, the pure parser/chunker, and the `ingest` node — with a
*real* HTTP fetcher — for HTML/web sources only.**

1. **`FetchProvider` protocol + `FetchedContent` DTO** (`services/ingestion/base.py`),
   mirroring the M5 `SearchProvider`. `FakeFetchProvider` (hermetic tests) **and**
   a real `HttpxFetchProvider`.
2. **Real fetcher now (not deferred).** Prior deferrals (M2/M5 adapters) were
   *network-availability* gates; that gate has lifted, so the deferral lifts for
   HTTP fetch — which is now both hermetically (`httpx.MockTransport`) and live
   (`@pytest.mark.integration`) testable. Gates of a *different* kind stay shut
   (see Deferred). The fetcher is **hardened** (first fetch of attacker-influenceable
   URLs): bounded timeout, response-size cap (5 MB), redirect cap, a
   `text/html`/`text/plain` content-type allowlist, and **no credentials/cookies**.
3. **Pure parser + chunker** (`parser.py`, `chunker.py`), no I/O:
   - `parse_html` uses the **stdlib `html.parser`** (no new dependency) to extract
     visible text (skips `script`/`style`/`head`/etc., unescapes entities,
     collapses whitespace). `beautifulsoup4`/`trafilatura` are a deferred *quality*
     upgrade, not a v1 need.
   - `chunk_text` uses a deterministic **fixed-size character window with overlap**
     (1200/200 default) — bounded chunk sizes for M7's context budget and future
     embeddings; `position` is the 0-based ordinal; `source_id` links back.
4. **`IngestionService(fetch_provider)`** — a deterministic tool (CLAUDE.md §4)
   that loops sources, **fetches WEB sources only** (skipping PDF/YouTube/repo in
   v1), and tolerates per-source fetch/parse failures (skip + log). It raises
   `IngestionError` only when **zero** chunks result — mirroring the discovery
   agent's "never advance on empty acquisition" contract.
5. **`ingest` node** between `acquire` and `reason` (`_make_ingest_node`,
   factory-closure DI, ADR 0004), single `chunks` channel write (no fan-out
   reducer — deferred to M7), wrapped by `_with_failure_handling` and plugged into
   the existing `_route_on_status` conditional-edge chain (ADR 0005, inherited
   for free). New topology: `plan → acquire → ingest → reason → publish`.
   `build_research_graph` / `run_research` gain one `ingestion` kwarg.

### `Chunk.parsed_via` provenance: deliberately DEFERRED

The schema-provenance question — add a typed `Chunk.parsed_via`, symmetric with
`Source.discovered_via` (ADR 0006) and `Evidence.extracted_via`? — was decided
**defer**, and the decision is recorded here rather than left silent:

- The load-bearing test from ADR 0006 is *integrity-provenance vs. incidental*.
  What made `discovered_via` pass was the **§11 evidence-vs-inference boundary**:
  an LLM could mint a fake `Source.url`, so a typed field structurally attests
  "tool-found." **That boundary does not exist for `Chunk`** — ingestion is
  deterministic/LLM-free, so a chunk is always tool-parsed; there is nothing to
  attest against.
- What remains is parse-method *fidelity* (HTML vs OCR vs caption). That is real
  — but only **once more than one parser exists**. In v1 (HTML only) the field
  would be the constant `"parse:html"` with nothing to discriminate, and its
  **shape is undetermined** (a later PDF/OCR parser may want page numbers or
  OCR-confidence, not a bare `str`) — so committing now risks a migration.
- Therefore: **defer to the first multi-parser milestone** (M-LP PDF/OCR), which
  is both when the field gains a real distinction and when its shape becomes
  determinable. Bonus: M6 stays **schema-change-free** (no `Chunk`/fixture/test
  churn, dodging the "mypy doesn't validate Pydantic required fields" trap).

This keeps the M5 precedent intact (typed provenance *where there is an inference
boundary to guard*), rather than contradicting it.

## Consequences

### Positive

- The pipeline now turns a topic into *fetched, parsed, chunked* content
  end-to-end (real `plan → acquire → ingest`), unblocking the reasoning half
  (M7 `Evidence` requires `Chunk`s).
- The fetcher is real and live-testable now, shrinking the eventual network batch;
  the parser/chunker are pure and fully unit-tested on fixtures.
- The `ingest` node inherits M4's failure routing with zero new error plumbing —
  validating the "real bands plug into the contract" promise of ADR 0005.
- No schema change → no construction-site churn.

### Negative

- **Stdlib HTML parsing is less robust than `bs4`/`trafilatura`** on messy
  real-world pages (boilerplate, weird markup). Acceptable for v1 (verifies
  wiring, not extraction *quality*); the upgrade is deferred to when quality, not
  wiring, is the bottleneck.
- **One more injected kwarg** on `run_research`/`build_research_graph` (now 3).
  Triggered follow-up: introduce a `ResearchDeps` container at M7 when the count
  crosses the threshold — not built now (avoids speculative abstraction).
- The single scalar-write ingestion is single-writer-safe only under the linear
  graph; concurrent per-source fetch + the list-channel reducer remain deferred
  (ADR 0002 §6, M7).

### Neutral

- Per-source failures land in logs, not the job-level scalar `error` (which stays
  single-writer-safe). A full failure (zero chunks) routes to `FAILED`.

## Deferred (with the gate that keeps each shut)

- **PDF / YouTube / repo / paper ingestion** → external-API/credential gate
  (Azure Document Intelligence, Nvidia OCR for scanned PDFs) — M-LP-class, each a
  new parser behind the same `FetchProvider`/parser seam.
- **`Chunk.parsed_via`** → unknown field shape until the first non-HTML parser.
- **Fan-out reducer + concurrent fetch** → topology-contingent (M7, ADR 0002 §6).
- **`bs4`/`trafilatura` extraction** → quality-not-wiring upgrade.
- **`ResearchDeps` dependency container** → triggered at M7 on the kwarg threshold.
- **SSRF hardening** → when M13 accepts user-supplied URLs (today URLs are
  search-sourced, not user-arbitrary).

## Alternatives considered

- **Fake-only fetcher (defer real fetch like M5).** Rejected: that mirrors the
  M-LP pattern *past its justification* — the network gate has lifted, so a real
  fetcher is both live- and hermetically-testable now.
- **Add `Chunk.parsed_via` now (symmetry with `discovered_via`).** Rejected for
  v1: no inference boundary to guard, constant value with one parser, and unknown
  shape — see Decision.
- **`beautifulsoup4` for v1 parsing.** Deferred: stdlib suffices for v1 text
  extraction; the dep is a quality upgrade, not a wiring need.

## References
- Related: [ADR 0006](0006-source-discovery-and-search-fabric.md) (search fabric
  mirror; typed-provenance precedent + load-bearing test), [ADR 0001](0001-research-state-and-provenance.md)
  (`Chunk`/`Evidence` schema, provenance pattern), [ADR 0002 §6](0002-langgraph-workflow-integration.md)
  (fan-out deferral), [ADR 0004](0004-node-dependency-injection.md) (factory-closure DI),
  [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by `ingest`).
- [CLAUDE.md](../../CLAUDE.md) §4, §5.3, §5.5, §7/§13, §11.
- [`docs/ROADMAP.md`](../ROADMAP.md) — M6 (this), M7 (extraction + fan-out), M-LP (PDF/OCR).
