# Band Deep-Dive: Knowledge Reasoning

> A node-by-node engineering trace of the **Knowledge Reasoning** band — the
> band that turns source-grounded `Evidence` into cross-checked `Verdict`s,
> plan-anchored `Finding`s, and a quality `Critique` (with a bounded revision
> loop). Companion to the high-level
> [Deep Research architecture write-up](deep-research-architecture.md), which
> covers the band's topology and revision cycle at altitude; this document zooms
> into the three nodes' internals — the agent/tool split, the layered §11 guards,
> and the failure contracts. Every claim is anchored to a `file:line`.

Band scope: three nodes — **`verify` → `synthesize` → `critique`** — with a
bounded `critique → synthesize` back-edge (the graph's first cycle). They write
the three fields of
[`KnowledgeReasoningState`](../../backend/app/schemas/research_state.py)
(`research_state.py:266`): `verdicts`, `synthesis`, and `critiques`.

This band is where the engine first produces **inference** rather than fact. The
unit of inference deepens at each node: a `Verdict` judges *one cluster of
evidence*; a `Finding` composes *multiple verdicts* into an answer to the plan; a
`Critique` judges *the composition itself*. The §11 guard is re-applied at each
layer, one rung up each time.

---

## 1. The pipeline at a glance

| Node | Kind (§4) | Tool | Reads | Writes | Code-derived structural fact |
| --- | --- | --- | --- | --- | --- |
| `verify` | **Agent + Tool** | `build_claim_blocks` | `acquisition.evidence` | `reasoning.verdicts` | distinct-source count → `SupportLevel` |
| `synthesize` | **Agent** | — | `plan`, `reasoning.verdicts` | `reasoning.synthesis` | `disputed` / `weakest_support` |
| `critique` | **Agent + Tool** | `uncovered_sub_question_ids` | `plan`, `reasoning.synthesis` | `reasoning.critiques` | coverage gap + accept/revise `decision` |

The shared idiom across all three: **the model references its inputs only by
local index** into a numbered list it was shown, and code resolves those indices
back to real objects — dropping any out-of-range index the model invented, and
dropping any output unit that resolves to nothing. The model authors judgment by
index or prose; code owns every id and every countable fact.

---

## 2. `verify` — local indices and a code-counted corroboration gate

[`CrossVerificationAgent`](../../backend/app/agents/cross_verification.py) is an
**agent + tool** node. The deterministic
[`build_claim_blocks`](../../backend/app/services/reasoning/claim_blocking.py)
tool groups related claims into bounded clusters (the lexical floor, bounding the
O(N²) cross-product); the agent judges each cluster (the semantic ceiling). The
blocker is a pure stdlib union-find over salient-token overlap, and its
determinism is part of the contract — "identical input yields identical blocks in
identical order" (`claim_blocking.py:11`), which is what lets the hermetic
`FakeProvider` tests script one response per block.

The §11 boundary is made structural **twice** here.

**First**, the model references evidence only by local index, and code resolves +
validates it. `_resolve_ids` drops any out-of-range index, and a verdict that
resolves to no supporting evidence is dropped entirely
(`cross_verification.py:144`):

```python
        supporting = self._resolve_ids(draft.supporting, members)
        contradicting = self._resolve_ids(draft.contradicting, members)
        if not supporting:
            logger.warning("cross-verification: dropping verdict with no valid supporting evidence")
            return None
```

**Second**, `CORROBORATED` is a code-counted fact, not a model label.
`_reconcile_support_level` computes the distinct-source set and **downgrades**
whatever the model proposed if the count is below two
(`cross_verification.py:202`):

```python
        distinct_sources = {ev.source_id for ev in supporting}
        if len(distinct_sources) >= 2:
            return SupportLevel.CORROBORATED
        if draft.support_level is SupportLevel.CORROBORATED:
            logger.info(
                "cross-verification: downgrading CORROBORATED -> SINGLE_SOURCE "
                "(supporting evidence spans %d distinct source(s))",
                len(distinct_sources),
            )
        return SupportLevel.SINGLE_SOURCE
```

Intra-source repetition (one source saying the same thing twice) can never
masquerade as corroboration. `CONTRADICTED` is symmetrically gated — it is
downgraded unless at least one *resolved* contradicting item exists
(`cross_verification.py:196`). The `SupportLevel` enum encodes this as a purely
*structural* axis kept orthogonal to claim strength, which `Verdict.confidence`
carries separately (`research_state.py:109`).

A `Verdict` references its evidence **by id** rather than re-snapshotting it
(`research_state.py:133`) — the inverse of `Evidence`'s attached-provenance
pattern, correct because `Evidence` is already self-documenting.

### Failure contract

`verify` raises `VerificationError` on empty input (defensive — `extract` already
raises on zero evidence upstream) and again if no verdict survives any cluster
(`cross_verification.py:103`, `:127`). Per-cluster model failures are tolerated
(skip + log, `cross_verification.py:119`). A *thin* result — few verdicts, all
single-source — is a valid result, not a failure.

---

## 3. `synthesize` — the grounding caveat the model cannot omit

[`SynthesisAgent`](../../backend/app/agents/synthesis.py) is a **pure agent**
(no tool): a single `LONG_CONTEXT` model call over the already-reduced verdict
set. There is no combinatorial explosion to bound (verdicts are ≈one per cluster)
and synthesis is inherently holistic, so no blocking tool is warranted
(`synthesis.py:10`).

The model authors prose plus **two separate index spaces** — `V#` into the
verdict list, `S#` into the sub-question list — each resolved against its own
list by a generic `_resolve`, so a verdict index can never be misread as a
sub-question (`synthesis.py:196`). A finding citing no resolvable verdict is
dropped (`synthesis.py:177`).

The **keystone guard** is the grounding summary, computed in code *after* the
drop check so it always floors over a non-empty set (`synthesis.py:183`):

```python
        return Finding(
            statement=draft.statement,
            detail=draft.detail,
            sub_question_ids=[sq.id for sq in sub_questions],
            supporting_verdict_ids=[v.id for v in supporting],
            disputed=any(v.support_level is SupportLevel.CONTRADICTED for v in supporting),
            weakest_support=min(
                supporting, key=lambda v: _SUPPORT_RANK[v.support_level]
            ).support_level,
            synthesized_via=synthesized_via,
        )
```

`disputed` and `weakest_support` are **code-derived from the cited verdicts**,
and the model-output DTO (`_FindingDraft`, `synthesis.py:86`) has *no field to
report them*. The `_SUPPORT_RANK` ordering (`synthesis.py:65`) makes
"most-cautious wins" explicit: a finding resting on a `CONTRADICTED` and a
`CORROBORATED` verdict floors to `CONTRADICTED`. The caveat travels forward
**non-omittably** — the schema marks these fields "never model-authored"
(`research_state.py:179`) — and the publishing band consumes them directly.

### Feed-forward on the revision back-edge

Synthesis takes an optional `prior_critique` (`synthesis.py:118`). On a revision
pass the critic's rationale and issue details are injected into the prompt
(`synthesis.py:224`) so re-synthesis *addresses* the critique rather than
re-running identical inputs — "without this feed-forward the revision loop would
be theater" (`synthesis.py:130`). The first pass passes `None` and reproduces
the original behavior exactly.

---

## 4. `critique` — the accept/revise decision is code's call

[`EditorialCriticAgent`](../../backend/app/agents/editorial_critic.py) is an
**agent + tool** node and the band's quality gate. The split is sharp:

- **Coverage** — which sub-questions have zero findings — is a pure set-difference
  owned by the
  [`uncovered_sub_question_ids`](../../backend/app/services/reasoning/coverage.py)
  tool. It walks `Finding.sub_question_ids` and returns the gap in plan order, so
  the gap list is itself priority-ranked (`coverage.py:18`). The model never
  computes coverage.
- The **agent** judges only what code cannot: redundancy, imbalance, unclear
  prose, and whether a finding's *wording* overstates past its code-attached
  `disputed` / `weakest_support` flags (`editorial_critic.py:64`).

The **decision is code-derived** (`editorial_critic.py:139`):

```python
        uncovered = uncovered_sub_question_ids(plan, synthesis)
        decision = CritiqueDecision.REVISE if (uncovered or issues) else CritiqueDecision.ACCEPT
```

The model gets no vote field, so it can never `ACCEPT` past an objective coverage
gap, nor hallucinate or suppress one. A model-raised issue that resolves to no
real finding *and* no real sub-question is dropped (`editorial_critic.py:161`) —
the model cannot raise an issue about something that does not exist.

A deliberate, subtle consequence: a **disputed finding is not a revise trigger**.
A contradicted topic is a valid, already-surfaced outcome, and re-synthesis
cannot un-dispute it — only coverage gaps and quality issues loop the band
(`editorial_critic.py:31`). The engine distinguishes "the world is genuinely
contested" from "the synthesis is sloppy," and only loops on the latter.

"Found nothing wrong" — zero issues, full coverage — is a valid `ACCEPT`, not a
failure (`editorial_critic.py:120`). The only hard failure is being handed a
synthesis with no findings (`editorial_critic.py:122`), a defensive wiring guard.

---

## 5. The revision loop — why it always terminates

The cycle is bounded by code, not by the model. The `critique` node is the **sole
writer** of a top-level `revision_iteration` counter, incremented once per pass
(`deep_research.py:231`). It lives top-level (not on `reasoning`) so the
`synthesize` node's rewrite of the `reasoning` channel on the back-edge can never
re-zero it (`deep_research.py:218`).

`_make_critique_router` owns termination — the model only *proposes*
`ACCEPT`/`REVISE`; the router decides whether a `REVISE` is permitted
(`deep_research.py:362`):

```python
    def route(state: ResearchState) -> Literal["failed", "revise", "accept", "exhausted"]:
        if state.status is JobStatus.FAILED:
            return "failed"
        if state.revision_iteration >= max_syntheses:
            return "exhausted"
        latest = state.reasoning.critiques[-1]
        return "revise" if latest.decision is CritiqueDecision.REVISE else "accept"
```

Once the counter reaches `max_syntheses` the router forces `exhausted`
regardless of the model's decision — the model can propose `REVISE` forever, but
it cannot keep the loop alive. An exhausted run **completes** with its best-effort
synthesis (it is not a failure — the "thin result is valid" principle applied to
the loop); that the budget was exhausted is recoverable from
`revision_iteration == max_syntheses` with the last critique still `REVISE`.

`critiques` is a **list**, not a single object (`research_state.py:266`): the
empty list is the "critic has not run" signal, and the list gives the loop a
per-iteration audit trail.

---

## 6. The band seam handed downstream

The reasoning band's output — the latest `Synthesis` of `Finding`s plus the
`Critique` trail — is exactly the input the Research Publishing band consumes. The
code-derived `disputed` / `weakest_support` flags from §3 and the
`uncovered_sub_question_ids` from §4 become the **non-omittable caveats** of the
published report and the unsafe-claim warnings of the creator packet. See the
[publishing band deep-dive](band-publishing.md) for how those structural facts
are surfaced rather than re-derived.

---

## References

- Nodes: [`backend/app/workflows/deep_research.py`](../../backend/app/workflows/deep_research.py)
  (`_make_verify_node` :170, `_make_synthesize_node` :188, `_make_critique_node` :210,
  `_make_critique_router` :345)
- Agents: [`cross_verification.py`](../../backend/app/agents/cross_verification.py),
  [`synthesis.py`](../../backend/app/agents/synthesis.py),
  [`editorial_critic.py`](../../backend/app/agents/editorial_critic.py)
- Tools: [`reasoning/claim_blocking.py`](../../backend/app/services/reasoning/claim_blocking.py),
  [`reasoning/coverage.py`](../../backend/app/services/reasoning/coverage.py)
- Schema: [`research_state.py`](../../backend/app/schemas/research_state.py)
  (`SupportLevel` :109, `Verdict` :125, `Finding` :155, `Synthesis` :186,
  `Critique` :240, `KnowledgeReasoningState` :266)
- ADRs: 0010 (cross-verification), 0011 (synthesis), 0012 (editorial critic + revision loop)
