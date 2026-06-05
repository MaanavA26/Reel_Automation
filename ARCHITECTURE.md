# Reel Automation — Architecture (as built)

This is the **as-built system map**: what exists on disk today and how the pieces
fit. It complements three other documents — read them for different depths:

- [`README.md`](./README.md) — the audience-facing overview and the signature idea.
- [`docs/adrs/`](./docs/adrs/) — every non-trivial decision, one ADR at a time (52+).
- [`docs/ROADMAP.md`](./docs/ROADMAP.md) — the **live status** source of truth (milestone-by-milestone).

If this file and an ADR ever disagree on *why*, the ADR wins; if they disagree on
*what is wired*, the code under `backend/app/` wins.

---

## Purpose

A production-grade agentic system that researches a topic like an analyst and
turns the result into a faceless short-form video (YouTube Shorts / Instagram
Reels / TikTok) — grounded in real, cross-checked sources rather than a single
prompt's hallucinations.

---

## The three layers (and what is built)

### 1. Agentic Intelligence Layer
Reasoning-heavy coordination: orchestration, critique, revision, routing. Realized
as the **LangGraph workflow** (`workflows/deep_research.py`) plus the judgment
**agents** (`agents/`) and the policy-driven **model fabric** (`services/llm/`).

### 2. Deep Research Layer — **COMPLETE (M1–M12)**
The first major component, organized into four bands (CLAUDE.md §5.5):

- **🛰️ Research Control** — plan a topic into prioritized sub-questions; orchestrate the run, route on status, own loop termination.
- **📥 Knowledge Acquisition** — discover sources (web / PDF / YouTube), fetch + parse + chunk, extract chunk-local grounded `Evidence`.
- **🔬 Knowledge Reasoning** — cross-verify claims across sources into `Verdict`s, synthesize plan-anchored `Finding`s, run an editorial critic with a **bounded revision loop**.
- **📤 Research Publishing** — assemble a cited `Report` + a creator packet (hooks, angles, narratives, key facts) for the video.

### 3. Media Production Layer — **built (adapters + pipeline)**
Turns the creator packet into a finished vertical video: script + shot-list →
voiceover → captions → B-roll → FFmpeg composition → SEO/thumbnail. TTS defaults
to **local Kokoro** (Apache-2.0, no service/key) with NVIDIA / HuggingFace /
OpenAI adapters behind an **agent-supervised router**.

Additional layers (publishing ops, analytics feedback, multi-tenant SaaS) are
introduced through ADRs; automation scaffolds for them exist but are not yet
closed into an unattended loop.

---

## The data-flow, end to end

```text
topic
  → plan ──→ acquire ──→ ingest ──→ extract ──→ verify ──→ synthesize ⇄ critique
                                                                          │ (bounded
                                                                          │  revision
                                                                          │  loop)
                                                                          ▼
                                                            report ──→ creator packet
                                                                          │
                                                                          ▼
                                          script + shot-list → voiceover → captions
                                                                          → B-roll
                                                                          → ffmpeg compose
                                                                          → SEO + thumbnail
                                                                          → safety gate
                                                                          → publish
```

A single typed `ResearchState` (`schemas/research_state.py`) threads the whole
graph. Each node returns a **partial dict**; the graph re-validates under a strict
schema (`extra="forbid"`). Conditional edges route off the `status` channel; the
critique→synthesize back-edge is the **only cycle**, bounded by a top-level
`revision_iteration` counter plus a `recursion_limit` backstop (ADR 0012).

---

## The signature pattern: evidence vs. inference, made *structural*

The repeated correctness mechanism across all five reasoning agents:

> **The model authors only judgment (prose + a choice among things it was shown).
> Code attaches every id, validates every reference, and derives every structural fact.**

So a citation to evidence that doesn't exist is *unrepresentable*; "corroborated"
requires ≥2 **distinct** real sources (code-counted, not model-claimed); a finding
cannot overstate its own grounding; a contradiction can't be buried by omission.
See the README table and ADRs 0009–0012.

---

## Agent vs. tool policy (CLAUDE.md §4)

The discipline that keeps the system explainable:

| Use an **agent** (LLM, via the model router) for | Use a **tool/service** (pure, deterministic, no LLM) for |
|---|---|
| planning, source discovery | fetching, parsing, chunking |
| evidence extraction, cross-verification | claim-blocking, citation assembly |
| synthesis, editorial critique, strategy | coverage/caveat derivation |
| TTS backend/voice supervision | SRT/VTT generation, ffmpeg composition, scheduling, publishing |

If a step is primarily deterministic, it is **never** modeled as an agent.

---

## The model fabric (`services/llm/`)

Provider-neutral routing so each *role* maps to a configured model:

- **Roles:** `PLANNING` · `EXTRACTION` · `LONG_CONTEXT` · `FALLBACK`.
- **Providers:** one OpenAI-compatible adapter (serves Groq / NVIDIA / HuggingFace / Ollama) + a Gemini-native adapter, behind a provider registry.
- **Composable decorators:** response cache, retry + fallback resilience, and an LLM-as-judge eval harness.
- **Budget guardrails** cap per-run / per-day spend so an automated channel can't run up a surprise bill.

No uncontrolled multi-model chatter — selection is policy-driven (CLAUDE.md §6).

---

## Repository layout (`backend/app/`)

```text
agents/        # judgment: planner, discovery, extraction, verification,
               #   synthesis, editorial critic, report, creator packet, tts supervisor
services/      # determinism: llm fabric, search, ingestion, reasoning tools,
               #   publishing, budget, jobs, eval
workflows/     # the LangGraph Deep Research graph (deep_research.py)
schemas/       # ResearchState + all DTOs (strict, extra="forbid")
media/         # tts · subtitles · visuals · composition · pipeline
scripting/ · seo/                       # script/shot-list builder, SEO + thumbnail
publishing/                             # YouTube uploader + TikTok/IG seams
scheduler/ · analytics/ · topics/ · channels/ · safety/   # the automation loop
api/ · core/ · client/ · cli/ · eval/ · tools/
```

Composition root: `services/composition.py` wires real providers by config and
returns `(deps, closables)` for lifecycle-safe teardown. Entry points:
`python -m app.cli.make_video "<topic>"` (or `make video TOPIC=…`) and
`POST /api/v1/videos`; `make doctor` is a backend-aware offline preflight.

---

## Design principles

- Production-grade, modular, component-first delivery — each component independently useful.
- Strict typing; typed workflow state; provenance preserved (`*_via` / `*_at` on every artifact).
- Minimal, reviewable diffs; clear layer boundaries; agent/tool separation never blurred.
- Hermetic-by-default tests (fake providers, no network/keys); live paths behind `@pytest.mark.integration`.
- Every architectural decision is recorded as an ADR.

---

## Status & the last mile

The Deep Research engine and every media/automation component **exist and are
tested hermetically**. The remaining work is the **last mile**: a single live
`topic → posted video` run with real keys + ffmpeg + the local Kokoro model files,
and validating the documented-not-yet-live wire contracts (YouTube upload, NVIDIA
TTS NIM shapes). By design that is configuration, not redesign. See
[`docs/ROADMAP.md`](./docs/ROADMAP.md) for the authoritative milestone status and
[`docs/getting-started.md`](./docs/getting-started.md) to run it locally.
