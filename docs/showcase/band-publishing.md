# Band Deep-Dive: Research Publishing

> A node-by-node engineering trace of the **Research Publishing** band — the
> band that turns the reasoning output into a source-grounded `Report` and a
> short-form `CreatorPacket` for the downstream media layer. Companion to the
> high-level [Deep Research architecture write-up](deep-research-architecture.md).
>
> **Orientation note:** the high-level write-up was authored when this band was a
> single `publish` lifecycle stub (its M11–M12 work was still roadmap). That work
> has since landed: the band is now two real nodes (`report`, `packet`) feeding a
> `publish` lifecycle terminal. This document traces the band **as built today**.
> Every claim is anchored to a `file:line`.

Band scope: two artifact nodes plus a terminal — **`report` → `packet` →
`publish`** — wired in
[`backend/app/workflows/deep_research.py`](../../backend/app/workflows/deep_research.py).
The first two write the two fields of
[`ResearchPublishingState`](../../backend/app/schemas/research_state.py)
(`research_state.py:514`): `reports` and `packets`. `publish` just marks the job
`COMPLETED` (`deep_research.py:282`) — the success mirror of the `failed` sink.

The band sits downstream of the reasoning revision loop's exit: the critique
router routes `accept`/`exhausted` to `report` (`deep_research.py:429`), so a
report over an *exhausted-unsatisfied* synthesis still ships — with the
unresolved-critique caveat carried forward.

---

## 1. The pipeline at a glance

| Node | Kind (§4) | Tools | Reads | Writes | Code-derived, non-omittable |
| --- | --- | --- | --- | --- | --- |
| `report` | **Agent + Tools** | `assemble_citations`, `derive_caveats` | `plan`, `reasoning`, `acquisition` | `publishing.reports` | bibliography + caveats |
| `packet` | **Agent + Tool** | `derive_creator_warnings` | latest `Report`, `synthesis.findings` | `publishing.packets` | key facts + warnings |
| `publish` | **Lifecycle terminal** | — | `state` | `status` | — |

The §11 pattern is held **one and two layers past** the reasoning band: the model
authors prose (title, abstract, sections; hooks, angles, narratives) referencing
findings only by local index `F#`; code resolves the ids; and the
*structural* outputs — the citation bibliography, the report caveats, the
creator-packet key facts and warnings — are **code-derived from the grounded
data**, with no model field to author or omit them.

A defining design choice: the structural lists range over the **full** findings
set, not the subset the prose happened to cite — otherwise the model could bury a
contradiction simply by not citing the disputed finding.

---

## 2. `report` — code owns the bibliography and the caveats

[`ReportAgent`](../../backend/app/agents/report.py) makes a single
`LONG_CONTEXT` call. The model authors a title, abstract, and sections, citing
findings by `F#`. Code resolves each section's indices, drops out-of-range ones,
drops a section that cites no real finding, and derives each section's
`sub_question_ids` from its cited findings — a **single** model index space, so
the two-index hazard the reasoning band guards against cannot arise here
(`research_state.py:349`).

The two structural artifacts are assembled by deterministic
[`services/publishing/`](../../backend/app/services/publishing/) tools and
attached in code (`report.py:133`):

```python
        cited_findings = [f for f in findings if f.id in cited_finding_ids]
        latest_critique = reasoning.critiques[-1] if reasoning.critiques else None
        citations = assemble_citations(
            cited_findings, reasoning.verdicts, acquisition.evidence, acquisition.sources
        )
        caveats = derive_caveats(findings, latest_critique)
```

Note the deliberate asymmetry on those two lines: **citations** cover the *cited*
findings (they are the report's references), but **caveats** range over the
*full* `findings` set — so an uncited disputed finding still surfaces
(`report.py:131`).

### Citations — walking the real provenance chain

[`assemble_citations`](../../backend/app/services/publishing/citations.py) is a
pure stdlib tool that walks `Finding → supporting_verdict_ids → Verdict →
evidence_ids → Evidence → source_id → Source`, grouping one `Citation` per
distinct source (`citations.py:25`). Two structural guarantees fall out of the
walk:

- **A fabricated citation is unrepresentable.** Dangling ids (a verdict/evidence/
  source not in the provided sets) are skipped (`citations.py:54`), and an
  accumulator with no resolved source is filtered out (`citations.py:68`) — the
  bibliography only ever references real, resolvable sources.
- **Conflicting sources are cited, not hidden.** The walk follows a verdict's
  *contradicting* evidence alongside its supporting evidence
  (`citations.py:52`), so a source that disputes a claim surfaces in the
  bibliography.

The `Citation` schema carries a **code-copied snapshot** (`source_url`, `title`),
the deliberate inverse of `Verdict`/`Finding`'s by-id refs, because the report is
the band's *export* artifact and must be readable in isolation
(`research_state.py:324`).

### Caveats — the publishing-band keystone

[`derive_caveats`](../../backend/app/services/publishing/caveats.py) projects the
already-code-derived reasoning facts into a non-omittable `Caveat` list: one per
disputed finding, one per single-source finding, then — from the latest critique
— the uncovered sub-questions, each carried-forward quality issue, and an
`UNRESOLVED_CRITIQUE` banner when the loop exhausted unsatisfied
(`caveats.py:95`):

```python
        if latest_critique.decision is CritiqueDecision.REVISE:
            caveats.append(
                Caveat(
                    kind=CaveatKind.UNRESOLVED_CRITIQUE,
                    detail=(
                        "The editorial review was not satisfied and the revision budget was "
                        "exhausted; treat these conclusions as provisional."
                    ),
                    critique_id=latest_critique.id,
                )
            )
```

By the router invariant, a *last* critique still reading `REVISE` means the
revision loop exhausted unsatisfied (see the
[reasoning band deep-dive](band-knowledge-reasoning.md) §5) — so this banner
fulfills ADR 0012's promise to carry an unsatisfied critique forward as a
non-omittable limitation. The `Caveat` schema marks the model as having "no field
to author or omit this" (`research_state.py:301`).

A finding's grounding is classified by a **single shared predicate**,
`finding_caveat_kind` (`caveats.py:27`) — reused by the creator packet (§3) so
the report and packet can never drift on what counts as unsafe.

### Failure contract

`report` raises `ReportError` on zero findings (defensive — synthesize raises
upstream) and if no section survives id-resolution (`report.py:107`, `:127`). A
thin or heavily-disputed report is **not** a failure: it ships with prominent
code-derived caveats.

---

## 3. `packet` — the same discipline, one layer up at the creator surface

[`CreatorPacketAgent`](../../backend/app/agents/creator_packet.py) is the
Short-Form Content Strategist (CLAUDE.md §5.6). It makes one `LONG_CONTEXT` call
producing hooks, content angles, and narrative options, each citing findings by
`F#`. The `Report` is given to the model as prose **context**, not a second index
space — so, again, a single index space and no two-index hazard
(`creator_packet.py:18`). A creative element resolving to zero real findings is
dropped (`creator_packet.py:198`).

The two structural lists are code-derived over the **full** findings set
(`creator_packet.py:140`):

```python
        key_facts = [
            KeyFact(
                statement=f.statement,
                finding_id=f.id,
                disputed=f.disputed,
                weakest_support=f.weakest_support,
            )
            for f in findings
        ]
        warnings = derive_creator_warnings(findings)
```

`KeyFact` is projected straight from each finding's code-derived grounding
(`research_state.py:470`) — the model authors no facts.
[`derive_creator_warnings`](../../backend/app/services/publishing/warnings.py)
emits one `CreatorWarning` per disputed or single-source finding, **reusing
M11's `finding_caveat_kind` predicate** — it imports the report's own predicate
rather than re-implementing it, so the two surfaces cannot drift
(`warnings.py:23`):

```python
from app.services.publishing.caveats import _finding_caveat_detail, finding_caveat_kind
```

The cross-reference back to a hook/angle/narrative is by **shared `finding_ids`**
(`research_state.py:395`): a warning ranges over all findings, but a consumer can
intersect its `finding_ids` with a creative element's code-resolved `finding_ids`
to learn which hook rests on a contradicted claim. So "a punchy hook can never
quietly rest on a disputed finding without the warning surfacing"
(`creator_packet.py:11`).

### Failure contract

`packet` raises `CreatorPacketError` on zero findings or if no creative element
survives id-resolution (`creator_packet.py:119`, `:134`). A thin / heavily-warned
packet is valid, not a failure — the same thin-is-the-product inversion as the
report.

---

## 4. Why both nodes are agents *and* the structural work is tools

The band is a textbook §4 split. Prose — narrative judgment, creative ideation —
is the **agent**. The bibliography, caveats, key facts, and warnings are
deterministic projections of grounded data, so they are **tools**
(`services/publishing/citations.py`, `caveats.py`, `warnings.py`), none of which
import an LLM. The schema makes the division explicit: `Report` "guarantees
citation + caveat *integrity*, not narrative *fidelity*" — the model-authored
abstract may still phrase a finding more confidently than its support warrants,
and "the non-omittable code-derived caveats is the structural counterweight"
(`research_state.py:369`).

`reports` and `packets` are **lists** (`research_state.py:514`) for the same
mechanical reason `critiques` is: each artifact has required fields (so it cannot
be a default), `| None` is barred by the schema convention, and the empty list is
the "this band step has not run" signal — which also yields a re-publish audit
trail for free.

---

## 5. The downstream handoff

The `CreatorPacket` is the band's terminal artifact and the **input contract** for
the Media Production layer (CLAUDE.md §3.3): hooks, angles, and beat-by-beat
narrative outlines are designed to feed TTS / subtitle / composition tooling,
with the code-derived `warnings` travelling alongside them so the media layer
need never amplify an unverified claim. That handoff contract and the media
layer's concrete adapters are out of scope for Deep Research and not yet wired —
both are tracked separately in [the roadmap](../ROADMAP.md).

---

## References

- Nodes: [`backend/app/workflows/deep_research.py`](../../backend/app/workflows/deep_research.py)
  (`_make_report_node` :238, `_make_packet_node` :260, `publish_node` :282)
- Agents: [`report.py`](../../backend/app/agents/report.py),
  [`creator_packet.py`](../../backend/app/agents/creator_packet.py)
- Tools: [`publishing/citations.py`](../../backend/app/services/publishing/citations.py),
  [`publishing/caveats.py`](../../backend/app/services/publishing/caveats.py),
  [`publishing/warnings.py`](../../backend/app/services/publishing/warnings.py)
- Schema: [`research_state.py`](../../backend/app/schemas/research_state.py)
  (`CaveatKind` :285, `Caveat` :301, `Citation` :324, `Report` :369,
  `CreatorWarning` :395, `KeyFact` :470, `CreatorPacket` :487,
  `ResearchPublishingState` :514)
- ADRs: 0017 (report generation), 0018 (creator packet)
