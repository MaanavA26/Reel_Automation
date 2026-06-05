# Band Deep-Dive: Knowledge Acquisition

> A node-by-node engineering trace of the **Knowledge Acquisition** band of the
> Deep Research engine — the band that turns a `ResearchPlan` into
> source-grounded `Evidence`. This is a **companion** to the high-level
> [Deep Research architecture write-up](deep-research-architecture.md); it
> assumes that document's framing (the four bands, the LangGraph topology, the
> evidence-vs-inference rule) and zooms into one band's concrete pipeline,
> agent/tool split, guard rails, and failure contracts. Every claim is anchored
> to a `file:line` you can open.

Band scope: three nodes — **`acquire` → `ingest` → `extract`** — wired in
[`backend/app/workflows/deep_research.py`](../../backend/app/workflows/deep_research.py).
They write `acquisition.sources`, `acquisition.chunks`, and
`acquisition.evidence` respectively, the three fields of
[`KnowledgeAcquisitionState`](../../backend/app/schemas/research_state.py)
(`research_state.py:99`).

---

## 1. The pipeline at a glance

| Node | Kind (§4) | Reads | Writes | Empty-input contract |
| --- | --- | --- | --- | --- |
| `acquire` | **Agent + Tool** | `state.plan` | `acquisition.sources` | raises `DiscoveryError` on zero sources |
| `ingest` | **Tool** (no LLM) | `acquisition.sources` | `acquisition.chunks` | raises `IngestionError` on zero chunks |
| `extract` | **Agent** | `acquisition.chunks`, `acquisition.sources` | `acquisition.evidence` | raises `ExtractionError` on zero evidence |

The through-line is a **monotonic narrowing of authorship**: the model authors a
*query* (`acquire`), authors *nothing* (`ingest`), then authors a *claim +
confidence* (`extract`). Every structural fact in between — URLs, ids,
provenance strings — is produced by code or by a tool, never by the model. That
is the §11 evidence-vs-inference rule, and this band is where it is established
for the first three times in the pipeline.

Each node is a **single-channel write**: it does all its work, then writes its
one substate field once via `state.acquisition.model_copy(update=...)`
(`deep_research.py:131`, `:147`, `:164`). Per-item fan-out and concurrency are
deliberately deferred to the checkpointer milestone (ADR 0002 §6), so the band
runs each step to completion before the next.

---

## 2. `acquire` — the model never authors a URL

[`SourceDiscoveryAgent`](../../backend/app/agents/source_discovery.py) is the
canonical "agent uses a tool" node. The split is the §11 boundary made
structural: the LLM authors a search *intent*; the search **tool** produces the
`url`/`title` that become a `Source`.

The model-output DTO has **no `url` field** — it cannot express one
(`source_discovery.py:39`):

```python
class _DiscoveryQuery(BaseModel):
    """One model-proposed search query (model-output DTO; no ids/urls)."""

    query: str
    source_type: SourceType
    rationale: str | None = None
```

The agent runs each query through the injected `SearchProvider` and constructs
each `Source` from the **tool's** result, attaching the discovery provenance
string in code (`source_discovery.py:80`):

```python
        for dq in plan_output.queries:
            results = await self._search.search(query=dq.query, limit=per_query_limit)
            for result in results:
                sources.append(
                    Source(
                        url=result.url,
                        type=result.source_type,
                        title=result.title,
                        discovered_via=f"search:{self._search.name}",
                        raw_metadata={"query": dq.query},
                    )
                )
```

`Source.discovered_via` is first-class, required provenance on the schema
(`research_state.py:59`) — its docstring calls it "the machine-readable encoding
of the evidence-vs-inference distinction: a `Source` is always tool-discovered,
never minted by an LLM" (`research_state.py:47`).

**Wiring state — honest note.** Two concrete `SearchProvider` adapters now exist
in the tree (`TavilySearchProvider`, `live.py:47`; `BraveSearchProvider`,
`brave_search.py:59`), but they are **not yet wired into the production
composition root**: `_build_search_provider` still raises a `CompositionError`
(`composition.py:63`) rather than ship a `Fake*` into a running service. So
end-to-end discovery runs against `FakeSearchProvider` in tests; production
discovery is one wiring change away, gated on choosing/key-ing an adapter. (The
high-level write-up's "live search is not wired in" claim therefore still holds
at the composition seam, even though the adapters themselves have landed.)

### Empty contract

Discovery refuses to advance the band on empty input, and does so **twice**
(`source_discovery.py:75`, `:92`): once if the model proposed no queries, once
if every query returned no source. The contract is "never advance on empty
acquisition" — the same contract the planner uses, propagated down the band.

---

## 3. `ingest` — a pure deterministic tool

[`IngestionService`](../../backend/app/services/ingestion/service.py) is a
**tool**, not an agent — no judgment, no LLM, no model role. It fetches, parses,
and chunks each `Source` by type. This is the §4 agent-vs-tool boundary at its
cleanest: a procedural transformation belongs in a service.

The per-type dispatch is a flat `if/elif` over `source.type`
(`service.py:66`): `WEB` is HTML-parsed, `PDF` goes through the injected
`PdfParser` (text layer only), `YOUTUBE` is transcribed **only when a
`TranscriptProvider` is injected** (otherwise it falls through to skip), and any
other type is skipped and logged.

### Failure contract — tolerate per-source, raise on total empty

The keystone of ingestion's robustness is its **two-level** failure handling
(`service.py:81`):

```python
                chunks.extend(chunk_text(text, source_id=source.id))
            except (FetchError, ParseError, TranscriptError) as exc:
                logger.warning("ingestion: skipping source %s: %s", source.id, exc)
                continue

        if not chunks:
            raise IngestionError("ingestion produced no chunks from any source")
        return chunks
```

A single source that fails to fetch, returns the wrong content-type, or fails to
parse is **skipped and logged** — one bad source does not sink the band. But if
*no* source yields a chunk, the service raises `IngestionError`
(`service.py:85`), honoring the same never-advance-on-empty contract as
`acquire`. This "tolerate-the-item, fail-the-empty-whole" pattern recurs at
every band node; ingestion is where it first applies to a tool rather than an
agent.

---

## 4. `extract` — provenance is code-attached, not model-asserted

[`EvidenceExtractionAgent`](../../backend/app/agents/evidence_extraction.py)
reads each chunk **in isolation** (one model call sees only that chunk's text,
`evidence_extraction.py:85`) and emits claims. This is the third agent to
enforce the §11 rule, and it does so by giving the model a DTO with **no
provenance fields** (`evidence_extraction.py:42`):

```python
class _ExtractedClaim(BaseModel):
    """Model-output shape for one claim (no ids/urls/timestamps)."""

    claim: str
    confidence: float = Field(ge=0.0, le=1.0)
```

The agent then builds each `Evidence` by **copying** the source/chunk identity
from the real objects it just fed the model (`evidence_extraction.py:93`):

```python
            evidence.extend(
                Evidence(
                    claim=claim.claim,
                    source_id=source.id,
                    source_url=source.url,
                    chunk_id=chunk.id,
                    chunk_text=chunk.text,
                    confidence=claim.confidence,
                    extracted_via=extracted_via,
                )
                for claim in output.claims
            )
```

Only `claim` and `confidence` come from the model; `source_id`, `source_url`,
`chunk_id`, `chunk_text`, and `extracted_via` are all code-attached. A claim can
**never** be misattributed to a source it did not come from, because the model
is never asked which source a claim came from — the code already knows (it
resolved `chunk.source_id` against a source registry built at
`evidence_extraction.py:73`).

This realizes the **attached-provenance** schema decision: `Evidence` carries a
self-contained snapshot (`source_url`, `chunk_text`) so a state dump is readable
without traversing the discovery registry (`research_state.py:79`).

### Two distinct failure modes

Extraction's `ExtractionError` covers two different conditions
(`evidence_extraction.py:29`):

1. **A chunk references an unknown source** — this is a *wiring bug*, not a data
   problem, so it raises immediately and hard (`evidence_extraction.py:80`):

   ```python
            source = registry.get(chunk.source_id)
            if source is None:
                raise ExtractionError(
                    f"chunk {chunk.id} references unknown source {chunk.source_id!r}"
                )
   ```

2. **No evidence from any chunk** — the never-advance-on-empty contract again
   (`evidence_extraction.py:106`).

In between, a *model* failure on a single chunk is tolerated (skip + log,
`evidence_extraction.py:90`) — the band-wide "one bad item must not fail the
whole band" rule, here applied to model calls.

---

## 5. The band seam: where fact ends and inference begins

The band's output, `acquisition.evidence`, is the engine's last layer of pure
**source-grounded fact** — a claim that a specific chunk of a specific source
literally states. The very next node, `verify`, opens the Knowledge Reasoning
band and produces the first **inference** (`Verdict`). The two live in separate
substates (`KnowledgeAcquisitionState.evidence` vs
`KnowledgeReasoningState.verdicts`) precisely so downstream bands can never
conflate them — see the
[reasoning band deep-dive](band-knowledge-reasoning.md) for the other side of
that seam.

---

## References

- Nodes: [`backend/app/workflows/deep_research.py`](../../backend/app/workflows/deep_research.py)
  (`_make_acquire_node` :119, `_make_ingest_node` :137, `_make_extract_node` :153)
- Agents/tools: [`source_discovery.py`](../../backend/app/agents/source_discovery.py),
  [`ingestion/service.py`](../../backend/app/services/ingestion/service.py),
  [`evidence_extraction.py`](../../backend/app/agents/evidence_extraction.py)
- Search adapters: [`search/live.py`](../../backend/app/services/search/live.py),
  [`search/brave_search.py`](../../backend/app/services/search/brave_search.py);
  wiring hole at [`composition.py`](../../backend/app/services/composition.py) :55
- Schema: [`research_state.py`](../../backend/app/schemas/research_state.py)
  (`Source` :44, `Chunk` :65, `Evidence` :76, `KnowledgeAcquisitionState` :99)
- ADRs: 0006 (source discovery + search fabric), 0008 (ingestion + fetch fabric),
  0009 (evidence extraction); 0013/0021 (live search adapters), 0014/0015 (PDF/YouTube ingestion)
