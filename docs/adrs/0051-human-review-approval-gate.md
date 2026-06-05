# ADR 0051: Human-review / approval gate

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Tech Lead, Council (schema-&-structure / agent-boundary-&-wiring / risk-first architects + advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

ADR 0041 shipped the **automated** pre-publish safety gate (`PrePublishGate`): a
pure, deterministic policy that turns the §11 code-derived caveats/warnings into a
`SafetyVerdict` (ALLOW / BLOCK / REVIEW). Its REVIEW rung is explicitly "do not
auto-publish, route to a human" — but there is **no surface for that human** to
act. A produced video that the automated gate (or a cautious operator) wants held
has nowhere to sit, no queue to list, and no way to record a person's
approve/reject decision before publish.

For a channel that may auto-publish, that is the missing human-in-the-loop
checkpoint between the automated gate and publish. CLAUDE.md §5.5 A names "quality
gates" as a Research Control responsibility and §3.4 names publishing operations as
a future layer; a human sign-off is the smallest, highest-leverage piece of that.

This is a **deterministic record-keeping** problem — minting a pending record,
listing the queue, transitioning a record to a decided state. The *judgment*
(whether to approve, and the reason) comes from a **human**, not an LLM. So per
CLAUDE.md §4 this is a **tool/service** plus a human, **not** an agent: there is no
reasoning to model in code.

## Decision

**Ship a deterministic `ReviewService` tool in a new self-contained
`backend/app/review/` package (sibling to `safety/`) that records a human review
decision as a stateful, timestamped event, plus three thin API routes to list
pending items and approve/reject them.**

### Distinct from `safety/` — the sharp contrast

The automated gate and this gate are deliberately *opposite* in shape, and that
contrast is why they are separate packages:

| | `safety/` (ADR 0041) | `review/` (this ADR) |
|---|---|---|
| Decided by | automated policy (code) | a **human** |
| Output | `SafetyVerdict` | `ReviewRecord` |
| Identity / time | **id-free, timestamp-free, pure** | **id'd, timestamped, stateful** |
| Recomputable? | yes (function of inputs) | no — a sign-off is an *event* |

A `SafetyVerdict` is a value object: equal inputs yield an equal verdict, so it
needs no id or clock. A human sign-off is the opposite — *who decided, and when*
is the whole point — so a `ReviewRecord` is durable-by-design, id'd, and carries
`decided_at` / `decided_via` provenance. Conflating the two would force one of them
to carry a shape it shouldn't.

### Package & schema (self-contained, no `schemas/` change)

1. `app/review/record.py` — `ReviewStatus` (`PENDING_REVIEW` / `APPROVED` /
   `REJECTED`, lowercase values like `JobStatus`) and `ReviewRecord` (strict,
   `extra="forbid"`, id-prefixed `rev_`). A record references the item under
   review by `subject_id` + a human-readable `subject_label` (for the pending
   list) rather than embedding the whole `VideoArtifact` — mirroring how
   `PublishCandidate` keeps `packet_id` for re-join. A local `_gen_id` keeps the
   package self-contained (the video package does the same).
2. `app/review/errors.py` — `ReviewNotFoundError` (unknown id) and
   `ReviewTransitionError` (illegal decision on an already-decided record),
   transport-agnostic so the router owns the HTTP mapping.
3. `app/review/base.py` — `ReviewStoreBackend`, the injectable `Protocol` seam
   (mirroring `services/jobs/base.JobStoreBackend`), so a future durable backend
   is a drop-in.
4. `app/review/service.py` — `ReviewService`, the in-memory store.

### §11 provenance — authored judgment vs. code-attached facts

5. The `reason` is the **human/caller-authored** judgment (the §11 analogue of
   "the model authors only judgment"); **code** attaches the id, the `status`
   transition, and the `decided_at` / `decided_via` provenance. A decision can
   therefore never claim a timestamp or transition it didn't actually make.

### Lifecycle & the legal transition

6. `submit(subject_id, …)` mints a `PENDING_REVIEW` record. `approve` (reason
   optional) and `reject` (reason **required**, non-blank) transition a pending
   record to a terminal state. The **only** legal transition is
   `PENDING_REVIEW → APPROVED|REJECTED`: deciding an already-decided record raises
   `ReviewTransitionError` (no silent overwrite — a sign-off is recorded once);
   deciding an unknown id raises `ReviewNotFoundError`.
7. Single-process, non-durable, `asyncio.Lock`-guarded — the same model `JobStore`
   documents (ADR 0031), held as a process-singleton on `app.state` (see
   `app.main`). A durable/cross-worker backend is **deferred** behind the
   `ReviewStoreBackend` seam (the ADR 0031 precedent).

### API (thin routes; producer wiring deferred)

8. `GET /api/v1/reviews?status=pending_review` (filter optional; `None` → all),
   `POST /api/v1/reviews/{id}/approve`, `POST /api/v1/reviews/{id}/reject` (reason
   in body). Routes are thin (CLAUDE.md §10): they delegate to the service and map
   its typed errors — `ReviewNotFoundError → 404`, `ReviewTransitionError → 409
   Conflict`, a blank reject reason → 422 via request validation.
9. **No create route.** `submit` is exposed only as a service capability — wiring
   the producer (the pipeline / post-safety-gate handoff) into it is a documented
   follow-up, the "capability before wiring" pattern of ADR 0041. The diff stays a
   reviewable, self-contained package.

## Consequences

### Positive

- The automated gate's REVIEW rung now has a **real human surface**: pending items
  can be listed, approved, or rejected with an audited reason — closing the
  human-in-the-loop checkpoint between `safety/` and publish.
- Clean agent/tool boundary (§4): deterministic record-keeping + a human is a
  tool, kept out of `agents/` and `workflows/`; no LLM.
- Auditable by construction (§11): code owns the id/timestamps/transition;
  the human owns only the reason, so a decision's provenance can't be forged.
- Self-contained, mirrors established seams (`safety/` package shape, `JobStore`
  store/protocol pattern), so it is trivially testable and explainable.

### Negative

- **Capability only — not wired.** The gate is a ready surface; nothing produces
  pending records yet. Wiring the video pipeline / safety-gate REVIEW outcome into
  `submit` (and gating publish on `APPROVED`) is a follow-up, kept out of scope so
  the diff stays self-contained.
- **Single-process, non-durable** (ADR 0031's limitation): a restart loses pending
  reviews and the queue is invisible across workers. A durable backend is deferred
  behind the `ReviewStoreBackend` seam.
- **No auth / no reviewer identity.** `decided_via` records the *channel*
  (`review:api`), not *who* — authn/authz and a per-user reviewer id are a later
  concern, out of scope here.

### Neutral

- The store is a process-singleton held on `app.state`, symmetric with the
  research/video job stores — neither an upside nor a risk, just the chosen
  lifecycle (it follows the established `JobStore` precedent exactly).
- The query filter is `?status=pending_review` (the `ReviewStatus` value), not
  `?status=pending` — `StrEnum` values are the lowercased member name, matching
  `JobStatus.QUEUED="queued"`. A reviewer expecting the shorthand should use the
  full enum value.

### Alternatives considered

- **Extend `safety/` to hold review state.** Rejected: it would force the pure,
  id-/timestamp-free `SafetyVerdict` to coexist with a stateful, timestamped human
  record — the two have opposite shapes (see the contrast table). A separate
  package keeps each honest.
- **Model the reviewer as an agent.** Rejected per §4: there is no reasoning to do
  in code — the judgment is the *human's*. An agent here would be "everything is an
  agent" sprawl (§11 bad pattern).
- **Add a create/submit route now.** Rejected per scope: producing pending records
  is the producer's job (pipeline / safety handoff); exposing a bare create route
  before that wiring exists invites misuse. Capability-before-wiring (ADR 0041).
- **Reuse `JobStatus` for the review states.** Rejected: review has its own
  vocabulary (`PENDING_REVIEW`/`APPROVED`/`REJECTED`) distinct from job lifecycle
  (`QUEUED`/`RUNNING`/…); a dedicated `ReviewStatus` keeps the contract legible.

## References

- ADR 0041 — pre-publish content-safety guardrail (the automated gate this sits beside).
- ADR 0031 — in-memory job store (the single-process, non-durable store precedent).
- ADR 0040 — SQLite job store (the durable-backend pattern a future review store would follow).
- CLAUDE.md §4 (agent vs. tool), §10 (thin routers), §11 (authored judgment vs. code-attached facts).
