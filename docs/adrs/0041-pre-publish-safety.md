# ADR 0041: Pre-publish content-safety guardrail

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (schema-&-structure / agent-boundary-&-wiring / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The Deep Research → Media pipeline now produces a publish-ready artifact set: a
code-grounded `Report` (M11) and a creator `CreatorPacket` (M12), both carrying
**code-derived, non-omittable** §11 limitations — the report's `Caveat`s
(`derive_caveats`) and the packet's `CreatorWarning`s (`derive_creator_warnings`).
Those signals encode *that* a published claim rests on contradictory sources or an
unresolved editorial critique, but nothing yet converts them into a **publish /
don't-publish decision**.

For a faceless short-form channel that may auto-publish, that gap is the channel's
biggest operational risk: an auto-posted video resting on a contradicted finding is
misinformation (platform strikes, reputational damage). CLAUDE.md §5.4 names
"unsafe/unverified claim warnings" as a first-class output and §11 names "no
distinction between evidence and inference" as a bad pattern — but a *warning* the
publisher can ignore is not a guardrail. We need the structural gate that ties the
caveats to the decision.

This is a **deterministic policy check** — no judgment, no reasoning, no LLM — so
per CLAUDE.md §4 it is a **tool/service**, not an agent. It must be explainable
(say *why* it blocked), configurable per call site (not via global `Settings`), and
pure (fully unit-testable, no I/O/clock).

## Decision

**Ship a deterministic `PrePublishGate` tool in a new `backend/app/safety/`
package that evaluates a `Report` + `CreatorPacket` + a `PublishCandidate`
(produced script/metadata) against a constructor-configured `GatePolicy` and
returns a typed `SafetyVerdict` (ALLOW / BLOCK / REVIEW + the reasons that drove
it). The gate *trusts* the upstream §11 code-derived caveats/warnings rather than
re-deriving them.**

### Package & schema (self-contained, no `schemas/` change)

1. `app/safety/verdict.py` — `SafetyVerdict` (a `decision` + a `reasons` list),
   `SafetyDecision` (ALLOW/REVIEW/BLOCK), `SafetyReason` (`kind` + `severity` +
   code-templated `detail`), `SafetyReasonKind`, and an internal `Severity`
   `IntEnum`. **Deliberately id-free and timestamp-free** (unlike the
   `*_at`/`*_via` artifacts in `research_state`): the gate is pure, so a verdict is
   a value object fully determined by its inputs and two equal evaluations compare
   equal — exactly what `caveats.py` already does (timestamp-free results).
2. `app/safety/gate.py` — `PublishCandidate` (the gate's own input DTO: `title`,
   `description`, `script_text`, and an **explicit** `disclaimer`), `GatePolicy`
   (a frozen dataclass of knobs), and `PrePublishGate`.

`PublishCandidate` lives in `app/safety/` because the gate cannot touch `schemas/`
and the produced script/metadata has no schema yet — an in-package DTO is in-scope
and correct.

### Policy: signal → severity (verb-aligned)

3. The verdict is the **maximum severity over the triggered reasons** (none →
   ALLOW). Every rule runs (no short-circuit) so a single pass surfaces *every*
   problem, making the verdict explainable and actionable at once. Mapping, read
   straight from the task verbs ("BLOCK when…", "flag…", "require…"):
   - `DISPUTED_FINDING` (a `DISPUTED_FINDING` caveat on the report **or** a disputed
     `CreatorWarning` on the packet) → **BLOCK**. The canonical misinformation case.
   - `UNRESOLVED_CRITIQUE` (the exhausted-revision banner) **without** a disclaimer
     → **BLOCK**; **with** a disclaimer → **REVIEW** (risk acknowledged, hold for a
     human rather than hard-block).
   - banned topic/keyword match → **REVIEW** (configurable to BLOCK).
   - below the source-grounding floor → **REVIEW**.
4. **Only `DISPUTED_FINDING` and `UNRESOLVED_CRITIQUE` drive the verdict.** The
   benign caveat kinds (`WEAK_SUPPORT`, `UNCOVERED_SUB_QUESTION`, `QUALITY_ISSUE`)
   are explicitly *not* block signals — otherwise a clean report with benign
   caveats could never reach ALLOW, and the guardrail would be unusable.

### §11 tie-in — trust, don't re-derive

5. The disputed/unresolved signals are read directly off `Report.caveats` and
   `CreatorPacket.warnings` (read-only import of `CaveatKind` from `schemas`). Those
   are code-derived and non-omittable *upstream*; the gate's value is converting
   them into a decision, not recomputing them. It does **not** pull in
   `Finding`/`Synthesis` (it doesn't have them and doesn't need them).

### Deterministic banned-keyword + grounding rules

6. Banned keywords match **case-insensitively** over the candidate's
   title+description+script surface, **whole-word by default** (so `"thorpe"` does
   not trip on `"Scunthorpe"`), with an opt-in substring mode. One reason per hit
   (sorted, for a stable order), at a configurable severity.
7. The grounding floor counts **distinct `source_id`s** across `report.citations`
   (mirroring the CORROBORATED ">=2 distinct sources" semantics — duplicate
   citations to one source cannot satisfy the floor), threshold via constructor
   (default 2; `0` disables).

### Configuration via constructor, never `Settings`

8. `GatePolicy` is a frozen dataclass passed to the gate's constructor (banned
   keywords, whole-word toggle, grounding threshold, the unresolved-critique block
   toggle, banned-keyword severity). This keeps the gate config-root-agnostic and
   trivially testable with bespoke thresholds (CLAUDE.md §10). No `config.py`,
   `main.py`, or `pyproject.toml` change.

## Consequences

### Positive

- The §11 caveats/warnings are now **load-bearing**: a contradicted finding or an
  unresolved critique structurally prevents auto-publish, rather than emitting a
  warning a publisher can ignore — the "don't post misinformation" guard.
- Fully explainable: every triggered rule surfaces a typed, human-readable reason;
  the decision is a transparent max-severity over them.
- Pure + hermetic: no LLM/IO/clock, so the policy is unit-testable to the verdict
  and the verdict is a comparable value object.
- Clean agent/tool boundary (§4): a deterministic policy check is a tool, kept out
  of `agents/` and `workflows/`.

### Negative

- **Keyword matching is shallow** (literal terms, not semantic): it catches named
  topics, not paraphrases, so it is a REVIEW-grade signal (human-in-the-loop) by
  default rather than a hard BLOCK. Semantic/topic classification is deferred.
- **Capability only — not wired.** The gate is a ready tool; nothing in the
  workflow calls it yet. Wiring it into the publishing band (or a publish endpoint)
  as a quality gate is a follow-up, kept out of scope here so the diff stays a
  reviewable, self-contained package (the M-LP "capability before wiring" pattern).
- The gate trusts upstream caveat/warning *integrity* — if a future change made
  those derivations omittable, the gate would inherit the gap. They are §11
  code-derived today, so this holds.

### Alternatives considered

- **Re-derive disputed/weak from `Finding`s in the gate.** Rejected: duplicates
  `caveats.py`'s code-derived predicate (drift risk) and couples the gate to the
  reasoning schema it doesn't need. Trusting the non-omittable upstream signals is
  the §11-consistent choice.
- **Block on any caveat present.** Rejected: a clean report with benign caveats
  could never publish; the gate must distinguish block-grade from benign kinds.
- **Scan script text for a disclaimer.** Rejected as fuzzy/unreliable; the
  "without a disclaimer" rule keys off an explicit `PublishCandidate.disclaimer`.
- **Configure via `Settings`.** Rejected per scope + §10; constructor args keep the
  gate config-root-agnostic and per-call-site configurable.
