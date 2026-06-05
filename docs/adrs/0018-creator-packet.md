# ADR 0018: Creator packet + the Short-Form Content Strategist

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (schema-&-structure / agent-boundary-&-wiring / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Milestone M12 closes the **Research Publishing band** (CLAUDE.md §5.5 band D) and
realizes the **Short-Form Content Strategist** agent (§5.6): turn the published
`Report` (M11) + the synthesized `Finding`s (M9) into a **creator packet** — the
creator-ready handoff artifact the downstream Media Production layer consumes. Per
§5.4 the packet must carry hook ideas, content angles, key facts, short-form
narrative options, and **unsafe/unverified-claim warnings**.

This is the engine's *most creative* and *most downstream* artifact: model prose
(a punchy hook) on inference (a report) on inference (findings) on inference
(verdicts) on fact (evidence). The standing §11 hazard — "no distinction between
evidence and inference," "no provenance on research outputs" — bites hardest here,
because a scroll-stopping hook is *designed* to sound confident. The overriding
concern: a hook/angle/narrative must not be able to quietly rest on a disputed or
single-source finding without an unsafe-claim warning surfacing.

## Decision

**Ship a `CreatorPacketAgent` (the Short-Form Content Strategist — creative prose)
+ deterministic `services/publishing/` warnings behind a dedicated `packet` node;
hold the §11 boundary one layer past M11, with the unsafe-claim warnings
code-derived over the FULL findings set.**

### Schema (additive)

1. `CreatorPacket` (`pkt_`): model-authored creative prose (`hooks`/`angles`/
   `narratives`); code-derived `key_facts` + `warnings`; `report_id` (re-join to
   the source `Report`); `published_via`. `HookIdea` / `ContentAngle` /
   `NarrativeOption` are model-prose sub-units carrying code-attached
   `finding_ids` (no id). `KeyFact` and `CreatorWarning` are code-derived
   sub-units (no id).
2. `ResearchPublishingState.packets: list[CreatorPacket]` — a list for the same
   forced reason as `reports`/`critiques`: a `CreatorPacket` has required fields
   (can't be a `default_factory` default) and `| None` is barred by ADR 0001; the
   empty list is the "packet has not run" signal.

### Agent / tool split (CLAUDE.md §4)

3. **`CreatorPacketAgent`** (agent — judgment) authors only creative prose and
   references findings by **local index (`F#`) — a single index space**. The
   `Report` is fed to the model as prose *context* (title/abstract/sections), **not
   a second index space**, so the M9 two-index cross-resolution hazard ADR 0017
   deliberately avoided cannot reappear. A single `LONG_CONTEXT` call over the
   already-reduced report + findings (short-form ideation is long-form
   summarization — the honest existing role, same as M9/M11; ADR 0003's mint bar
   governs *new* roles, this is reuse).
4. **`services/publishing/warnings.py`** (deterministic) owns the unsafe-claim
   warnings: `derive_creator_warnings` projects each disputed / single-source
   finding into a `CreatorWarning`. It **reuses** M11's exact finding-level
   predicate — `caveats.finding_caveat_kind` was extracted from `derive_caveats`
   for this purpose, so the report's caveats and the packet's warnings can never
   drift on what counts as unsafe (no copy-pasted `if disputed / elif
   SINGLE_SOURCE` block).

### §11 made structural — the keystone (warnings over the FULL findings set)

5. **`key_facts` are code-derived, never a model field** — projected straight from
   every `Finding` (statement + the code-derived `disputed`/`weakest_support`), so
   a packet's fact sheet cannot overstate or invent past the synthesized findings.
6. **`warnings` are code-derived over the FULL `synthesis.findings`, not the
   findings the creative elements happen to cite** — the load-bearing correctness
   point, the direct M11 mirror. An *element-driven* derivation (warn only on
   findings a hook references) would let the model bury a contradiction by simply
   not referencing the disputed finding in any hook/angle/narrative. So warnings
   iterate findings, exactly as `derive_caveats` does. The cross-reference back to
   a specific element is by **shared `finding_ids`** (a warning's findings ∩ an
   element's code-resolved findings) — no per-element warning field, and an
   *uncited* disputed finding still surfaces a warning. The model gets no field to
   author or omit warnings — they are **non-omittable by construction**.

### Wiring

7. **Dedicated `packet` node** (9th `ResearchDeps` field, `strategist`), slotted
   between `report` and the `publish` lifecycle terminal: `report`'s `continue`
   arm re-points to `packet`; `packet` routes via the existing `_route_on_status`
   to `publish`, which stays the **lifecycle terminal** (the success mirror of the
   `failed` sink, kept free of judgment work per CLAUDE.md §4). The node reads
   `publishing.reports[-1]` (the latest report) + `reasoning.synthesis.findings`.
   The `_recursion_limit` backstop tail term grows `+2 → +3` (report + packet +
   publish).

### Failure / empty contract

8. `CreatorPacketError` (→ FAILED) only on empty findings (defensive — report
   generation raises upstream on empty) or zero surviving creative elements (call
   failed / every element cited only fabricated indices). A **thin / all-disputed /
   heavily-warned packet is valid, not a failure** — it ships with prominent
   code-derived warnings (the M8/M9/M10/M11 "thin is the product" inversion at the
   creator surface).

## Consequences

### Positive

- The pipeline now produces a **complete creator packet** end-to-end — the bridge
  artifact from the Deep Research layer to the future Media Production layer (§3.3).
- Unsafe-claim warning non-omittability is structural, not advisory: a punchy hook
  cannot quietly rest on a contradicted/thin finding, and warnings + key facts are
  code-derived at the most-creative surface.
- The agent/tool split keeps creative prose (judgment) and warnings/key-facts
  (deterministic) cleanly separated — the §4 showcase, one layer past M11.
- The shared `finding_caveat_kind` predicate means the report and the packet
  agree by construction on what is unsafe.
- `publish` stays a clean lifecycle terminal; the Publishing band is complete.

### Negative

- **Creative prose fidelity is not code-guaranteed**: a hook's wording can still
  phrase a finding more confidently than its support warrants (the OVERSTATED-prose
  limit M9/M10/M11 acknowledged). The non-omittable code-derived warnings (tied by
  shared `finding_ids`) are the structural counterweight; a packet-critic loop is
  rejected as scope creep (CLAUDE.md §7/§17) — the warning already travels.
- One more `ResearchDeps` field (now 9) — flat frozen dataclass, low cost.

### Neutral

- Dropped finding indices land in logs, not the scalar `error`.
- `published_via = f"packet:{model.model}"`, symmetric with the other bands.
- `key_facts` mirrors every finding 1:1 at v1 (no ranking/selection) — honest and
  minimal; a "top-N salient facts" selection is deferred to a real consumer need.

## Deferred (with the gate)

- **Per-element warning *projection*** (annotating each hook with the subset of
  warnings that intersect its `finding_ids`) → when a consumer (the API/frontend)
  needs it; the shared-`finding_ids` join already carries the information.
- **Timelines / analogies** (§5.4) → when a downstream media template consumes
  them; not invented speculatively (the M9 narrative-layer discipline).
- **Key-fact ranking / selection** → when a consumer needs fewer than all facts.
- **A `CreatorPacket`-specific `ModelRole`** → when policy routes it to a distinct
  model (ADR 0003 bar).

## Alternatives considered

- **Warnings over the cited subset only.** Rejected: lets the model bury a
  contradiction by not referencing it — the exact silent-wrong this band prevents;
  warnings range over the full findings set (the M11 keystone, mirrored).
- **A per-element `warnings` field on each hook/angle.** Rejected: would make the
  warning omittable (the model could under-populate it) and duplicate state; the
  shared-`finding_ids` cross-reference is non-omittable and single-sourced.
- **Model authors key facts / warnings.** Rejected: invented fact / omittable
  warning at the most-creative surface; code-deriving both is the §11-consistent
  choice.
- **A second (report + section) index space for the model.** Rejected: reintroduces
  the M9 two-index hazard ADR 0017 avoided, and warnings derive from findings'
  flags regardless; the report is prose context, findings are the single index
  space.
- **Re-implement the disputed/single-source predicate in `warnings.py`.** Rejected:
  drift risk between the report's caveats and the packet's warnings; the shared
  `finding_caveat_kind` extracted from M11 is the single source of truth.
- **Overload `publish` with packet generation.** Rejected: mixes judgment with
  lifecycle marking; the dedicated `packet` node feeding a clean `publish` terminal
  mirrors the M11 `report`-node choice.
- **A packet-critic / revision loop for creative prose.** Rejected: scope creep;
  the warning travels non-omittably by construction, so a loop would chase wording,
  not integrity.

## References
- Related: [ADR 0001](0001-research-state-and-provenance.md) (no-None-defaults →
  the list; attached-provenance rationale), [ADR 0003](0003-model-router-llm-fabric.md)
  (role reuse), [ADR 0004](0004-node-dependency-injection.md) (factory-closure DI),
  [ADR 0005](0005-workflow-error-handling.md) (failure wrapper inherited by
  `packet`), [ADR 0011](0011-synthesis.md) (the single-call + local-index +
  code-derived-fact template), [ADR 0012](0012-editorial-critic.md) (the
  thin-is-valid inversion), [ADR 0017](0017-report-generation.md) (the
  code-derived-over-the-full-set caveat keystone this mirrors one layer up; the
  single-index-space choice; the dedicated-node-feeding-`publish` pattern;
  `finding_caveat_kind` extracted here for reuse).
- [CLAUDE.md](../../CLAUDE.md) §4, §5.4 (output expectations), §5.5 (band D),
  §5.6 (Short-Form Content Strategist), §7/§17 (no flashy complexity), §11
  (evidence vs inference, provenance).
- [`docs/ROADMAP.md`](../ROADMAP.md) — M11 (report), M12 (this).
