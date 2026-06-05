# ADR 0042: Channel / brand profiles

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council
- **Supersedes:** none
- **Superseded by:** none

## Context

Reel Automation is built to run **several faceless channels** consistently, not
one. Each channel has a stable identity — a niche and topic seeds, a target
platform set, a TTS voice, a narrative tone/persona, a posting cadence, banned
topics, and public branding (handle, hashtags) — that the downstream steps must
read to stay on-brand: topic sourcing (what to research / avoid), scripting (how
narration should sound), TTS (which voice to synthesize), and SEO/publishing
(platforms, hashtags).

CLAUDE.md §3.4 names a future *style / brand memory* layer and asks that new
layers be introduced via an ADR. This is that layer's first concrete slice. The
question is the *shape* of the config object and the *seam* of its store, in a
repo that already has two distinct, established idioms.

## Decision

We will add a new, self-contained `backend/app/channels/` package (a deterministic
**config / tool** layer per CLAUDE.md §4 — it holds the brand contract; the
reasoning agents *read* it, they are not modelled here):

1. **`ChannelProfile` (schema).** A strict (`extra='forbid'`), id-prefixed
   (`chan_`) Pydantic model mirroring `app.schemas.research_state` and
   `app.media.schemas`. Controlled vocabularies (`StrEnum`) for the fields a
   consumer branches on — `Platform`, `NarrativeTone`, `PostingCadence` — per the
   §6 "policy/enum over free strings" preference; free text only where nuance is
   irreducible (`persona`). `name`/`niche`/`tts_voice_id` are required (a
   downstream step cannot proceed without them) and `platforms` is non-empty (a
   channel with no target is not runnable). Branding is a distinct `Branding`
   sub-model so it can grow without widening the profile surface.

2. **`ChannelStore` as a `@runtime_checkable` `Protocol`** (the
   `TTSProvider`/`SearchProvider` idiom), **not** the single-class `JobStore`
   idiom. The task requires both a store and a test fake; a fake only earns its
   place behind a shared seam. `InMemoryChannelStore` is the process-local
   production default; `FakeChannelStore` is the pre-seedable, call-recording test
   double consumer tests inject. A durable backend (`SqlChannelStore`) is a
   documented follow-up that implements the same `Protocol` and drops in without
   touching consumers.

3. **Async, in-memory, single-process.** The seam is async (mirroring `JobStore`
   and the deferred durable backend, which would be async) so adopting persistence
   later is not a signature break. Mutations are serialized by one `asyncio.Lock`,
   exactly as `JobStore`. The store owns the `updated_at` bump
   (`model_copy(update=...)` then schema re-validate), keeping lifecycle
   bookkeeping out of the schema.

`_gen_id` is copied locally (not imported from `app.schemas`), keeping the layer
decoupled — the same decision the Media layer documents (ADR 0019), kept in sync
by the shared `prefix + 64-bits-hex` convention.

Scope boundary: this is the config object and its store only. No API router, CLI,
or wiring into the research/media pipelines, and no `config.py`/`main.py`/
`schemas/` changes — those are follow-ups once a consumer exists.

## Consequences

### Positive

- A single typed contract keeps a channel on-brand across topic sourcing,
  scripting, TTS, and SEO — the §3.4 brand-memory layer gets a real foundation.
- The `Protocol` seam makes the durable backend a true drop-in and gives every
  consumer a substitutable, call-recording fake for hermetic tests.
- Mirrors three established repo conventions (strict id-prefixed schema, the
  provider/fake seam, the `JobStore` lock + `updated_at` bump), so it reads as
  native to a reviewer.

### Negative

- In-memory and single-process: a restart loses all profiles and a profile created
  on one worker is invisible to another (the same limitation `JobStore` carries —
  ADR 0031). Acceptable for the development/demo target; the durable backend is
  deferred.
- Controlled vocabularies (`Platform`, `PostingCadence`) must be edited to add a
  new platform or cadence — a deliberate trade of flexibility for type safety.

### Neutral

- The store is async though the in-memory impl has no real I/O — chosen for seam
  stability, not present need.

## Alternatives considered

### Option A — Single concrete store, no Protocol (the `JobStore` idiom)

One `ChannelStore` class, no seam. Rejected: the task requires a test fake, and a
fake without a shared seam is dead code — consumer tests would just instantiate
the real store. The `Protocol` is what makes the fake (and the deferred durable
backend) substitutable.

### Option B — Put `ChannelProfile` in `app/schemas/`

Rejected on scope (`schemas/` is out of bounds here) and on precedent: the Media
layer keeps its DTOs in `app/media/schemas.py`, decoupled from the research
schema. A brand-memory layer is analogously its own package.

### Option C — Free-text platform/tone/cadence strings

Simpler model, but pushes parsing/validation onto every consumer and invites
drift. Rejected per CLAUDE.md §6's controlled-vocabulary preference.

## References

- CLAUDE.md §3.4 (future style/brand memory), §4 (agents vs tools), §6
  (controlled vocab), §9.5 (document decisions)
- Related: [ADR 0001](0001-research-state-and-provenance.md) (id scheme, strict
  schema), [ADR 0019](0019-media-production-layer.md) (local `_gen_id` copy /
  layer decoupling), [ADR 0031](0031-async-job-store.md) (in-memory store +
  `asyncio.Lock` + `updated_at` bump, durable-backend deferral)
