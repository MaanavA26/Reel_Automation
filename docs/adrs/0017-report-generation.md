# ADR 0017: Report generation + the Research Publishing band

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (schema-&-structure / agent-boundary-&-wiring / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

> ADR numbering note: 0013–0016 are assigned to the parallel M-LP / API
> milestones (live search, PDF, YouTube ingestion, API surface) developed
> concurrently on sibling branches; this Publishing-band milestone takes 0017.

## Context

Milestone M11 opens the **Research Publishing band** (CLAUDE.md §5.5 band D):
turn the reasoning output — `Finding`s (M9), `Verdict`s (M8), `Critique`s (M10) —
into a structured, source-grounded research **report** with citations. Until now
the `publish` node was a lifecycle stub marking `COMPLETED`. The report is the
engine's **final, most-polished, most-downstream, most-public** artifact, built
on inference (findings) on inference (verdicts) on fact (evidence) — so the
overriding concern is that it must not (a) cite a source that doesn't exist, (b)
present a disputed/single-source finding as settled, or (c) drop the caveats the
upstream bands carefully carried forward. CLAUDE.md §11 names "no provenance on
research outputs" and "no distinction between evidence and inference" as bad
patterns; this is the milestone where they would most visibly bite.

## Decision

**Ship a `ReportAgent` (prose) + deterministic `services/publishing/` tools
(citations, caveats) behind a dedicated `report` node; hold the §11 boundary one
layer past M9/M10, with the caveat list code-derived over the FULL findings set.**

### Schema (additive)

1. `Report` (`rpt_`): model-authored `title`/`abstract`/`sections`; code-derived
   `citations` + `caveats`; `published_via`. `ReportSection` (`sec_`):
   model-authored `heading`/`narrative` + code-attached `finding_ids` +
   code-derived `sub_question_ids`. `Citation` (`cit_`) and `Caveat`
   (+ `CaveatKind`) are pure sub-units (no id).
2. `ResearchPublishingState.publishing` with `reports: list[Report]` — a list for
   the same forced reason as `critiques`: a `Report` has required fields (can't be
   a `default_factory` default) and `| None` is barred by ADR 0001; the empty list
   is the "publish has not run" signal.

### Agent / tool split (CLAUDE.md §4)

3. **`ReportAgent`** (agent — judgment) authors only prose and references findings
   by **local index (`F#`) — a single index space**. Section `sub_question_ids`
   are derived in code from the cited findings, so the M9 two-index
   cross-resolution hazard cannot arise. A single `LONG_CONTEXT` call over the
   already-reduced findings set (report writing is long-form summarization — the
   honest existing role, same as M9; not the raw-evidence grouping ADR 0010
   rejected; the ADR 0003 mint bar governs *new* roles, this is reuse).
4. **`services/publishing/` tools** (deterministic) own all structure:
   - `citations.assemble_citations` walks the provenance chain
     `Finding → Verdict → Evidence → Source` and emits one `Citation` per distinct
     source, **snapshotting** `source_url`/title (the deliberate inverse of the
     by-id pattern — the report is the band-D *export* artifact that leaves the
     container, so ADR 0001's attached-provenance rationale applies most to it).
     Dangling ids are skipped; a disputed finding's *contradicting* evidence is
     cited too so the conflict is visible.
   - `caveats.derive_caveats` projects the already-code-derived upstream facts
     into the report's limitations.

### §11 made structural — citation + caveat *integrity*

5. **Citations are code-derived, never a model field** — so a published report
   citing an invented source is unrepresentable (the M8/M9 guard at the final
   surface). Sections cite findings by `F#`; out-of-range dropped; a section
   resolving to zero real findings dropped (the M9 drop-empty guard).
6. **Caveats are code-derived over the FULL `synthesis.findings`, not the cited
   subset** — the load-bearing correctness point. If caveats ranged only over
   cited findings, the model could bury a contradiction by simply not citing the
   disputed finding in any section. Disputed/weak caveats therefore range over all
   findings; uncovered sub-questions, quality issues, and the unresolved-critique
   banner come from `critiques[-1]`. The model gets no field to author or omit
   caveats — they are **non-omittable by construction**.
7. **The exhausted-revision banner.** ADR 0012 (M10b) deferred "carry the
   unsatisfied critique forward as a non-omittable caveat" to its consumer; M11
   is that consumer. By the router invariant, at publish
   `critiques[-1].decision is REVISE` ⟺ the revision loop *exhausted unsatisfied*
   (the only path to publish with a REVISE verdict is the `exhausted` arm). So
   `derive_caveats` emits an `UNRESOLVED_CRITIQUE` banner exactly when the last
   critique reads REVISE — no `max_syntheses` plumbing into the report node, no
   new field. A polished report over an exhausted run cannot read as accepted.

### Wiring

8. **Dedicated `report` node** (8th `ResearchDeps` field), not an overload of
   `publish`: the critique router's `accept`/`exhausted` arms re-point to
   `report`; `report` routes via the existing `_route_on_status` to `publish`,
   which stays the **lifecycle terminal** (the success mirror of the `failed`
   sink). M12's creator-packet node will slot in beside `report` feeding the same
   terminal — keeping judgment work out of lifecycle marking (CLAUDE.md §4). The
   `_recursion_limit` backstop gains the one extra tail node.

### Failure / empty contract

9. `ReportError` (→ FAILED) only on empty findings (defensive — synthesize raises
   upstream) or zero surviving sections (call failed / all sections cited only
   fabricated findings). A **thin/all-disputed/exhausted report is valid, not a
   failure** — it ships with prominent code-derived caveats (the M8/M9/M10/M10b
   "thin is the product" inversion at the final surface). A report node that
   raised on "still flagged REVISE" would convert a legitimate exhausted-shipped
   run into a spurious FAILED — explicitly avoided.

## Consequences

### Positive

- The pipeline now produces a **complete, source-grounded, caveated research
  report** end-to-end — the Deep Research engine's headline deliverable.
- Citation integrity and caveat non-omittability are structural, not advisory:
  the report cannot cite a fabricated source nor bury a contradiction, and an
  exhausted-unsatisfied run is surfaced, not hidden.
- The agent/tool split keeps prose (judgment) and provenance/caveats
  (deterministic) cleanly separated — a strong §4 showcase.
- `publish` stays a clean lifecycle terminal that M12 can also feed.

### Negative

- **Prose fidelity is not code-guaranteed**: the `abstract`/`narrative` sentences
  can still phrase a finding more confidently than its support warrants (the
  OVERSTATED-prose limit M9/M10 acknowledged). The non-omittable code-derived
  caveats are the structural counterweight; a report-critic loop is explicitly
  rejected as scope creep (CLAUDE.md §7/§17) — the caveat already travels.
- The markdown/HTML renderer is **deferred** (no consumer this milestone — the
  typed `Report` is the export; the API/frontend consume it directly).
- One more `ResearchDeps` field (now 8) — flat frozen dataclass, low cost.

### Neutral

- Dropped indices / dangling-id skips land in logs, not the scalar `error`.
- `published_via = f"report:{model.model}"`, symmetric with the other bands.

## Deferred (with the gate)

- **Markdown/HTML rendering** → when a consumer needs a rendered body.
- **Creator packet** (hooks, angles, narrative options, key facts) → M12.
- **`Report`-specific `ModelRole`** → when policy routes it to a distinct model.

## Alternatives considered

- **Caveats over the cited subset only.** Rejected: lets the model bury a
  contradiction by not citing it — the exact silent-wrong this band prevents.
- **Model authors citations / caveats.** Rejected: fabricated citation /
  omittable caveat at the most-public surface; code-deriving both is the
  §11-consistent choice.
- **By-id citations (no snapshot).** Rejected: the report is the export artifact;
  ADR 0001's attached-provenance rationale (readable in isolation) applies most
  to the thing that leaves the container.
- **Overload `publish` with report generation.** Rejected: mixes judgment with
  lifecycle marking and forces a restructure at M12; a dedicated `report` node
  feeding a clean `publish` terminal is extensible.
- **A report-critic / second revision loop for prose.** Rejected: scope creep;
  the caveat travels non-omittably by construction, so a second loop would chase
  wording, not integrity.

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (attached-provenance
  rationale → snapshot citations; no-None-defaults → the list), [ADR 0003](0003-model-router-llm-fabric.md)
  (role reuse), [ADR 0004](0004-node-dependency-injection.md) (factory-closure DI),
  [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by
  `report`), [ADR 0011](0011-synthesis.md) (the single-call + local-index +
  code-derived-fact template this mirrors), [ADR 0012](0012-editorial-critic.md)
  (the exhausted-critique-as-caveat promise M11 fulfills).
- [CLAUDE.md](../../CLAUDE.md) §4, §5.4 (output expectations), §5.5 (band D),
  §7/§17 (no flashy complexity), §11 (evidence vs inference, provenance).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M11 (this), M12 (creator packet).
