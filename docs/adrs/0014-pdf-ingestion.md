# ADR 0014: PDF Ingestion (second parser behind the seam)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (PDF-library / provenance-now-or-defer / scanned-PDF
  architects — run as structured analysis; no sub-agent tool was available) + advisor
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0008 shipped the ingestion seam (`FetchProvider` + pure parser/chunker +
`IngestionService` + `ingest` node) for **HTML/web sources only**, and explicitly
deferred PDF/YouTube/repo parsers, each "a new parser behind the same
`FetchProvider`/parser seam." This milestone (M-LP.4) builds the **first of those
deferred parsers — PDF** — which makes it the moment ADR 0008 flagged twice:

1. **the first multi-parser milestone** — the trigger ADR 0008 set for
   reconsidering the deferred `Chunk.parsed_via` provenance field;
2. **a dep-gated parser** — PDF text extraction has no stdlib equivalent (unlike
   HTML), so it needs an external library, and the build sandbox cannot
   `pip install` (no PyPI egress), the same gate that kept M-LP shut.

PDF ingestion is **deterministic** — fetch, extract text, normalize, chunk — so
per CLAUDE.md §4 it is *tool/service* work, not an agent (no LLM).

## Decision

**Add a `PdfParser` behind the existing seam, route `SourceType.PDF` in
`IngestionService`, and keep it fully hermetically testable without the
dependency.**

1. **`PdfParser` protocol** (`services/ingestion/base.py`, alongside
   `FetchProvider`): a pure, *synchronous* `parse(content: bytes) -> str`
   (parsing is CPU-bound; network I/O stays in `FetchProvider`). Symmetric with
   the existing parser shape; raises `ParseError` (reused from `parser.py`) on
   failure so `IngestionService`'s existing per-source skip path catches it.
2. **`PypdfParser`** (`services/ingestion/pdf_parser.py`) backed by **`pypdf`** —
   a lightweight, pure-Python, actively-maintained, MIT-licensed library with no
   native build step (chosen over `pdfminer.six` (heavier, slower) and
   `PyMuPDF`/`pdfplumber` (AGPL / native deps)). The import is **lazy (inside
   `parse`, never `__init__`)**, so default construction works in the offline
   sandbox; a missing dependency surfaces as a `ParseError` at parse time →
   per-source skip (graceful degradation), not an import crash at wiring time.
3. **Pure `normalize_pdf_text(pages) -> str`** extracted as a standalone function
   (page-join + whitespace-collapse, mirroring the HTML parser's normalization so
   chunks are shape-consistent across source types). Unit-tested directly with no
   `pypdf` needed.
4. **`FakePdfParser`** (`services/ingestion/fakes.py`): replays scripted text per
   PDF bytes; unmapped bytes raise `ParseError` (exercises the skip path). Makes
   the entire PDF route testable offline.
5. **`IngestionService` routes PDF.** `__init__` gains an **optional**
   `pdf_parser: PdfParser | None = None`, defaulted to `PypdfParser()` — so the
   existing positional `IngestionService(fetch)` callers (including the
   out-of-scope graph wiring) keep working unchanged. PDF sources go through the
   injected parser; WEB stays HTML-parsed; other types are skipped. Chunking
   reuses the **existing `chunk_text`** untouched.
6. **`HttpxFetchProvider` allowlist widened** to include `application/pdf`
   (the size cap + no-credentials posture are unchanged), so the *live* fetch
   path delivers PDF bytes to the parser instead of rejecting its own
   content-type. (Hermetic tests use `FakeFetchProvider`, which has no allowlist,
   so they are unaffected either way — but a feature silently rejecting its own
   content-type would be incoherent, so the one-line widening is made and
   documented rather than left as a latent gap.)

### `Chunk.parsed_via` provenance: reconsidered, deliberately RE-DEFERRED

ADR 0008 deferred `Chunk.parsed_via` "to the first multi-parser milestone … when
the field gains a real distinction and when its shape becomes determinable."
That milestone is now, so the field was reconsidered — and the decision is to
**re-defer to a dedicated schema PR**, for two reasons:

- **The distinction now exists** (`parse:html` vs `pdf:pypdf` vs future
  `pdf:ocr`), so the *content* objection from ADR 0008 has lifted — this is a
  genuine reconsideration, not a rubber-stamp. But the field is pure provenance:
  the feature is fully functional without it, and because all research state is
  **in-memory** there is **no migration cost** to adding it later.
- **Scope + shape.** `Chunk.parsed_via` lives in `research_state.py`, which is
  **explicitly out of this PR's scope** (the task bounds edits to
  `services/ingestion/` + `pyproject.toml`). Adding it cleanly is a
  *cross-cutting* change — `chunk_text`'s signature plus **every `Chunk(...)`
  construction site** (HTML and PDF paths, fixtures, tests) — that belongs in a
  focused schema PR where its shape can be settled deliberately (a bare `str`
  now, vs. room for page numbers / OCR-confidence the OCR path may want). Bundling
  it here would both breach scope and risk a half-considered shape.

This keeps the ADR 0006/0008 precedent intact (typed provenance arrives in its
own considered change, not smuggled into a parser PR).

### Scanned / image-only PDFs → OCR stays deferred

A PDF with no text layer (scanned image) yields empty extraction. `PypdfParser`
treats that as a `ParseError` ("no extractable text") → per-source skip, never a
crash and never a silent empty chunk. **OCR** (Azure Document Intelligence /
Nvidia, per ADR 0008) is the real recovery path and remains deferred behind the
same external-credential gate — it would slot in as a *third* parser
(`pdf:ocr`) behind this same `PdfParser` seam.

## Consequences

### Positive

- The pipeline now ingests a second source type end-to-end; the seam ADR 0008
  promised ("a new parser behind the same seam") is validated by a real second
  implementation.
- The dependency gate is handled without blocking: lazy import + `FakePdfParser`
  + pure `normalize_pdf_text` make the whole route hermetically testable now; the
  real path is `@pytest.mark.integration`, ready for a network-enabled run.
- Backward-compatible: optional constructor param, reused `chunk_text`, reused
  `ParseError`/skip path, no schema change.

### Negative

- **`pypdf` is a new runtime dependency** (uninstallable in the current sandbox);
  a per-module mypy `ignore_missing_imports` override carries the offline build
  (a harmless no-op once `pypdf`, which ships `py.typed`, is installed).
- **Text-layer only.** Scanned PDFs are skipped, not read, until the deferred OCR
  parser lands.
- The default `PypdfParser()` means a PDF source in an environment without
  `pypdf` is *silently skipped* (logged) rather than loudly failing — the
  intended graceful-degradation behaviour, but worth noting.

### Neutral

- One more allowed content-type on the fetcher (`application/pdf`); the hardening
  caps are otherwise unchanged.

## Deferred (with the gate that keeps each shut)

- **`Chunk.parsed_via`** → a dedicated schema PR (cross-cutting + out of this
  PR's scope; zero migration cost while state is in-memory).
- **OCR for scanned/image-only PDFs** → external-credential gate (Azure DI /
  Nvidia); a third `pdf:ocr` parser behind this same seam.
- **YouTube / repo / paper parsers** → still deferred (ADR 0008), each behind the
  same seam.
- **Richer PDF extraction** (layout, tables, per-page provenance) → quality
  upgrade, not a wiring need; co-travels with the `parsed_via` shape decision.

## Alternatives considered

- **`pdfminer.six` / `PyMuPDF` / `pdfplumber`.** Rejected for v1: heavier, slower,
  or AGPL / native-build dependencies. `pypdf` is the lightweight pure-Python fit
  (CLAUDE.md §6/§7); a richer extractor is a deferred quality upgrade.
- **Add `Chunk.parsed_via` now (ADR 0008's stated trigger).** Reconsidered and
  rejected *for this PR*: out of scope (`research_state.py`), cross-cutting, and
  shape-sensitive — see Decision. Re-deferred to a focused schema PR, not
  dropped.
- **Import `pypdf` at module/`__init__` time.** Rejected: explodes default
  construction offline. Lazy import keeps wiring offline-safe and turns a missing
  dep into a graceful per-source skip.
- **Skip PDF until a network-enabled run installs `pypdf`** (mirror M-LP).
  Rejected: the *parser seam, routing, normalization, and tests* are all
  buildable and hermetically verifiable now; only the live extraction defers, so
  the bulk of the value lands now (same split as ADR 0007/0008's real-vs-fake).

## References
- Related: [ADR 0008](0008-source-ingestion-and-fetch-fabric.md) (the ingestion
  seam this extends; the deferred-`parsed_via` decision reconsidered here),
  [ADR 0006](0006-source-discovery-and-search-fabric.md) (typed-provenance
  precedent), [ADR 0001](0001-research-state-and-provenance.md) (`Chunk` schema),
  [ADR 0007](0007-openai-compatible-llm-adapter.md) (real-vs-fake adapter split
  under the network gate).
- [CLAUDE.md](../../CLAUDE.md) §4 (agent-vs-tool), §5.3 (source types), §6/§7, §11.
- [`docs/ROADMAP.md`](../ROADMAP.md) — M-LP.4 (this), M6 (HTML ingestion).
