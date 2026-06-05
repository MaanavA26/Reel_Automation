# Product Vision — Reel Automation

> What Reel Automation is *for*, what is built today, and the honest path from
> here to a faceless short-form **income engine**. This is the product companion
> to the engineering write-ups under [`docs/showcase/`](showcase/) and the
> operator references in [`docs/operations.md`](operations.md) and
> [`docs/configuration.md`](configuration.md).
>
> **Accuracy contract.** Everything called "built" below is wired and exercised
> in the repository today. Everything called "deferred," "vision," or "last mile"
> is *not* — it is named so a reader is never misled. When two senses of a word
> collide (notably "publishing"), this doc disambiguates them explicitly.

---

## 1. The product in one line

Give Reel Automation a **topic**; get back a **verified, source-grounded research
packet** designed to become a **faceless vertical short-form video** (YouTube
Shorts, Instagram Reels, TikTok) — eventually published on a schedule and
improved by an analytics feedback loop.

The wager is simple: the quality of a short-form channel is gated by the quality
of its *research and angle*, not its rendering. So the system is built
**research-first** — a deep, cross-checked research engine — and then extends
forward into media and distribution.

---

## 2. The end-to-end pipeline (the full vision)

```
topic
  │
  ▼
[ Deep Research ]   plan → discover sources → ingest → extract evidence →
  │                 cross-verify → synthesize → critique↻ → REPORT → CREATOR PACKET
  ▼
[ Script ]          narrative options + hooks + beats from the creator packet
  │
  ▼
[ Media Production ] narration (TTS) + captions + B-roll → FFmpeg → vertical MP4
  │
  ▼
[ Distribution ]    publish to platforms on a schedule
  │
  ▼
[ Feedback ]        analytics → topic/angle selection → back to the top
```

Each arrow is a deliberate handoff with a typed contract, not an "AI does the
rest" hand-wave. The seam that matters most commercially is the
**creator packet → media** handoff: the research engine emits exactly the
artifacts (hooks, angles, narrative options, key facts, and *non-omittable
unsafe-claim warnings*) that a media pipeline needs to assemble a video without
re-deriving or fabricating anything.

---

## 3. Honest current state — built vs. last-mile vs. vision

The single most important thing a reader should take away is **which stages are
real**. Two words ("publishing," "packet") mean different things in different
layers; this table keeps them straight.

| Pipeline stage | Status | What exists today |
| --- | --- | --- |
| **Deep Research** (topic → report → creator packet) | **Built (M1–M12)** | The full LangGraph pipeline `plan→acquire→ingest→extract→verify→synthesize→critique↻→report→packet→publish`, runs end-to-end in the hermetic test suite. See the [architecture write-up](showcase/deep-research-architecture.md). |
| **Research Publishing band** (the `Report` + `CreatorPacket` artifacts) | **Built (M11–M12)** | Source-grounded report with a code-walked citation bibliography + non-omittable caveats; short-form creator packet with hooks/angles/narratives + code-derived warnings. Traced in [band-publishing.md](showcase/band-publishing.md). |
| **Script** (narrative options, hooks, beats) | **Built** (inside the creator packet) | The `CreatorPacket` carries `NarrativeOption`s and `HookIdea`s; the `MediaPipeline` tool selects a narrative and lays out beats. No separate "script agent" — the packet *is* the script contract. |
| **Media Production** (TTS, captions, B-roll, FFmpeg compose) | **Seam-built; not wired end-to-end** | The media layer is scaffolded behind provider-neutral protocols, with concrete adapters already built: FFmpeg composition, an HTTP TTS adapter, a stock B-roll (Pexels) retrieval adapter, deterministic SRT/VTT subtitles, and a `MediaPipeline` mapping `CreatorPacket → MediaPlan`. There is **no end-to-end runner** that takes a topic to a rendered MP4 yet, and real TTS/visual provider selection is network-gated. |
| **Distribution** (publish to YouTube/Instagram on a schedule) | **Vision (no code)** | A future layer (CLAUDE.md §3.4 "social media operations / publishing management"). Nothing in the repository posts to a platform or schedules anything today. |
| **Feedback** (analytics → topic selection) | **Vision (no code)** | A future layer (CLAUDE.md §3.4 "analytics and feedback loop"). |

### Disambiguating "publishing"

- **Research Publishing band** = the *built* band-D nodes (`report`, `packet`)
  that produce research artifacts. This is **not** social-media posting.
- **Distribution / social publishing** = the *vision* layer that would post
  finished videos to platforms and schedule them. This has **no code** today.

When the roadmap or CHANGELOG says "publishing," it means the Research Publishing
band unless it explicitly says "social" or "distribution."

### The "last mile" — the two named wiring holes

The research engine is complete, but two seams keep a *fully automatic* run from
happening on `main` without writing code (both are deliberate, both surface as
loud errors — see [operations.md](operations.md#known-limitations)):

1. **Search is not wired into the running service.** Concrete `SearchProvider`
   adapters (Tavily, Brave) exist, but the composition root
   (`backend/app/services/composition.py`) does not connect one, so the HTTP
   `/research` endpoint returns **503**. The hermetic pipeline runs against a
   fake search provider; a live run needs that adapter wired in.
2. **The media layer has no end-to-end runner.** Every media tool exists behind
   its protocol, but nothing chains *creator packet → narration → captions →
   B-roll → FFmpeg → file* automatically with real providers yet.

Closing these two seams is what turns "a research engine you can demo" into "a
video you can post."

---

## 4. Path to first revenue

Revenue from faceless short-form comes from **volume of watchable, trustworthy
videos** on a monetizable channel. The path is staged so each step is shippable
and demonstrable on its own (CLAUDE.md §2, component-first).

1. **Live research run (nearest milestone).** Wire a `SearchProvider` into the
   composition root so `/research` returns a real `Report` + `CreatorPacket` over
   live sources. This alone is a sellable product: *source-grounded research
   briefs with honest caveats* — useful to any creator or analyst, video or not.
2. **First rendered video.** Add an end-to-end media runner that takes the
   creator packet through the already-built `MediaPipeline` (TTS → captions →
   B-roll → FFmpeg) to a vertical MP4. Output is a finished, human-reviewable
   short. Revenue here is *manual upload* of machine-made videos to a channel.
3. **Distribution + scheduling.** Add the social-publishing layer (CLAUDE.md
   §3.4) to post on a cadence. This is where the channel becomes a hands-off
   asset and ad/affiliate revenue compounds with volume.
4. **Feedback loop.** Add analytics ingestion so topic and angle selection learn
   from what actually performs — the difference between a content firehose and a
   *growing* channel.

The deliberate ordering keeps a **human in the loop** early (review the brief,
review the video) and removes them only as each stage earns trust — which also
keeps the unverified-claim warnings doing their job: a faceless channel that
publishes a fabricated "fact" loses the channel.

---

## 5. The SaaS angle (future)

The same engine generalizes from "run my channel" to "run anyone's channel."
The architecture already leans this way:

- **Provider-neutral fabrics.** The LLM router, search fabric, and media
  protocols all select backends by configuration, not code — the foundation for
  per-tenant model/cost policies (a free-model tier vs. a premium tier is a
  routing change, not a rewrite). See the [model-fabric notes](showcase/deep-research-architecture.md#6-supporting-architecture-briefly).
- **Typed, isolated state.** Each research run is one self-contained
  `ResearchState` with attached provenance — the unit a multi-tenant job store
  would own per customer.
- **Honest, structural quality guarantees.** The "model proposes, code decides"
  discipline (no fabricated URLs, code-counted corroboration, non-omittable
  caveats) is exactly the trust property a paid product must guarantee — it is a
  feature, not just engineering hygiene.

Multi-tenancy itself — auth, per-tenant job stores, billing, durable/queued
execution — is **not built** (the current job store is single-process and
in-memory by design; see [operations.md](operations.md#single-process-in-memory-job-model-no-job-store)).
It is a future direction the architecture is *prepared for*, not a current
capability.

---

## 6. Where to go next

- Engine internals: [`docs/showcase/deep-research-architecture.md`](showcase/deep-research-architecture.md)
  and the per-band deep-dives.
- Run it yourself: [`docs/getting-started.md`](getting-started.md).
- Operate / configure: [`docs/operations.md`](operations.md),
  [`docs/configuration.md`](configuration.md).
- Build sequence and what's landing: [`docs/ROADMAP.md`](ROADMAP.md).
