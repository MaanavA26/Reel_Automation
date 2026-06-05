# Getting Started — Your First Research Run

> A concrete, honest runbook for getting Reel Automation working on your machine.
> It takes you to the **furthest point reachable on `main` today without writing
> code**: a *live research plan* against a real LLM, plus the full Deep Research
> pipeline exercised hermetically by the test suite.
>
> **Read this first — the honest ceiling.** The product vision is "topic → video"
> (see [`docs/product-vision.md`](product-vision.md)). The Deep Research engine is
> complete (topic → report → creator packet), but two seams are not yet wired, so
> a *fully automatic topic-to-video* run is **not** available from a single
> command yet:
>
> - The HTTP `/research` endpoint returns **503** — no production search provider
>   is wired into the running service.
> - There is **no full-pipeline CLI** and **no video output** on `main`.
>
> This runbook is precise about what each step actually does. It will not hand
> you a command that does not exist. The deferred pieces are named at the end.

---

## Contents

- [Prerequisites](#1-prerequisites)
- [Step 1 — install the backend](#2-step-1--install-the-backend)
- [Step 2 — configure an LLM provider](#3-step-2--configure-an-llm-provider)
- [Step 3 — run a live research plan](#4-step-3--run-a-live-research-plan)
- [Step 4 — run the full pipeline (hermetically)](#5-step-4--run-the-full-pipeline-hermetically)
- [Step 5 — boot the API (optional)](#6-step-5--boot-the-api-optional)
- [Limitations — the "first video" last mile](#7-limitations--the-first-video-last-mile)

---

## 1. Prerequisites

- **Python 3.11+** on your `PATH` (`python3 --version`).
- A **free LLM API key** from any OpenAI-compatible backend (Groq is the
  preconfigured default in the env template — a free key at
  <https://console.groq.com> works).
- For step 3 (a live model call) you need **network access**. Steps 1, 2, and 4
  are fully offline.

---

## 2. Step 1 — install the backend

From the repository root:

```bash
make setup
```

This creates a project-local virtualenv at `backend/.venv`, upgrades `pip`, and
installs the backend in editable mode with dev extras
(`pip install -e "./backend[dev]"`). See [`operations.md`](operations.md#running-locally-makefile--venv)
for the full set of `make` targets (`fmt`, `lint`, `types`, `test`, `check`).

---

## 3. Step 2 — configure an LLM provider

The model fabric routes each role (planning, extraction, long-context, fallback)
to a provider chosen by configuration. **Only the OpenAI-compatible provider is
wired today** — the default `DEFAULT_PROVIDER=anthropic` has no adapter and will
fail, so you must override it.

Copy the template and edit it:

```bash
cp backend/.env.example backend/.env
# then edit backend/.env
```

The template is preconfigured for Groq — paste your key into `REEL_AUTOMATION_API_KEY`:

```bash
REEL_AUTOMATION_DEFAULT_PROVIDER=openai-compatible
REEL_AUTOMATION_BASE_URL=https://api.groq.com/openai/v1
REEL_AUTOMATION_API_KEY=gsk_your_key_here
REEL_AUTOMATION_PLANNING_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_EXTRACTION_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_LONG_CONTEXT_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_FALLBACK_MODEL=llama-3.1-8b-instant
```

Any OpenAI-compatible backend works by changing `BASE_URL` + `API_KEY` + model
ids only (OpenRouter, Together, local Ollama) — see the provider matrix in
[`configuration.md`](configuration.md#llm-providers). Model ids change over time;
verify the current ids at your provider before use.

> `backend/.env` is gitignored and loaded relative to the process working
> directory, so run the commands below **from `backend/`**.

---

## 4. Step 3 — run a live research plan

This is the first real, end-to-end exercise of a live model call on `main`: the
`ResearchPlannerAgent` decomposes a topic into prioritized, non-overlapping
sub-questions and prints the resulting `ResearchPlan` as JSON.

```bash
cd backend
.venv/bin/python -m app.cli.plan "why fusion ignition is hard"
```

You will get a structured `ResearchPlan` — the **first node** of the Deep
Research pipeline (the `plan` node), running against your configured model. This
is the only stage with a dedicated CLI today.

> If you omit the topic, it defaults to "the James Webb Space Telescope". If the
> call fails, re-check your key and that `DEFAULT_PROVIDER=openai-compatible`.

---

## 5. Step 4 — run the full pipeline (hermetically)

The complete pipeline — `plan → acquire → ingest → extract → verify → synthesize
→ critique↻ → report → packet → publish` — is exercised end-to-end by the test
suite using `Fake*` providers and `httpx.MockTransport`, so it needs **no
network and no keys**. This is how the full topic → report → creator-packet flow
is validated today:

```bash
cd backend
.venv/bin/pytest                       # hermetic; integration tests excluded
```

To exercise the live LLM/search adapters against real services (network +
credentials required), opt into the integration marker:

```bash
.venv/bin/pytest -m integration
```

The end-to-end pipeline test lives in `backend/tests/integration/test_pipeline_e2e.py`
and is the best place to read how a full run is assembled from a fake-backed
`ResearchDeps` bundle.

---

## 6. Step 5 — boot the API (optional)

You can boot the FastAPI app and hit the health endpoint:

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
# health: http://localhost:8000/api/v1/health
```

The `POST /api/v1/research` endpoint, however, returns **HTTP 503** under any
configuration today — no production `SearchProvider` is wired into the
composition root (see [Limitations](#7-limitations--the-first-video-last-mile)
and [operations.md](operations.md#known-limitations)). The health endpoint works
regardless of provider configuration; the research endpoint does not yet.

A Docker/Compose path (backend + frontend) is documented in
[`operations.md`](operations.md#running-with-docker--compose).

---

## 7. Limitations — the "first video" last mile

This runbook deliberately stops short of "produce your first video" because that
path is not wired on `main` yet. The exact gaps, so nothing surprises you:

- **No full-pipeline CLI.** Only the `plan` node has a CLI (`app.cli.plan`). The
  full topic → creator-packet run is reachable today only through the test suite
  (step 4) or by writing your own driver that injects a `ResearchDeps` bundle.
- **`/research` returns 503.** The HTTP path can't run a job end-to-end because
  search is unwired in the composition root (`backend/app/services/composition.py`).
  There is no environment-only way to activate search today.
- **No video output.** The Media Production layer is seam-scaffolded with several
  concrete adapters built (FFmpeg composition, HTTP TTS, stock B-roll retrieval,
  SRT/VTT subtitles) and a `MediaPipeline` that maps a `CreatorPacket → MediaPlan`,
  but **no end-to-end runner** chains them from a creator packet to a rendered
  MP4 with real providers. Real TTS/visual provider selection is network-gated.
- **No distribution or analytics.** Posting to platforms, scheduling, and the
  analytics feedback loop are forward-looking layers with **no code** today
  (CLAUDE.md §3.4). See [`docs/product-vision.md`](product-vision.md#3-honest-current-state--built-vs-last-mile-vs-vision).
- **Single-process, in-memory job model.** Even once search is wired, a research
  run holds the HTTP connection open and state lives only for the request; there
  is no durable job store yet ([operations.md](operations.md#single-process-in-memory-job-model-no-job-store)).
- **Ingestion coverage.** When a live run is wired, ingestion handles **WEB**
  (HTML) and **PDF** (text layer). YouTube has an adapter but it is not wired;
  scanned/image-PDF OCR is unsupported ([configuration.md](configuration.md#ingestion-providers)).

For where these gaps sit on the build sequence, see
[`docs/ROADMAP.md`](ROADMAP.md) and the product vision's
[path to first revenue](product-vision.md#4-path-to-first-revenue).
