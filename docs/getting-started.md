# Getting Started — Clone to Your First Video

> A concrete, honest runbook for cloning Reel Automation and producing your
> **first short-form video** end-to-end on your own machine. Every command, env
> var, and path below matches the real code — no aspirational steps.
>
> The path is: **clone → `make setup` → `make check` → configure `.env` →
> `make doctor` → `make video`** → an MP4 at `backend/renders/vid_*.mp4`.

---

## Contents

- [What a live run actually needs](#1-what-a-live-run-actually-needs)
- [Per-device one-time setup](#2-per-device-one-time-setup)
- [Step 1 — install the backend (`make setup`)](#3-step-1--install-the-backend-make-setup)
- [Step 2 — hermetic sanity check (`make check`)](#4-step-2--hermetic-sanity-check-make-check)
- [Step 3 — configure your keys (`.env`)](#5-step-3--configure-your-keys-env)
- [Step 4 — preflight (`make doctor`)](#6-step-4--preflight-make-doctor)
- [Step 5 — make your first video (`make video`)](#7-step-5--make-your-first-video-make-video)
- [Troubleshooting](#8-troubleshooting)
- [What this does and does not do yet](#9-what-this-does-and-does-not-do-yet)

---

## 1. What a live run actually needs

A live `make video` chains the Deep Research engine and the Media Production
layer, so it needs **four external services plus one binary**. This is the
honest, complete list — `make doctor` (Step 4) checks every one before you spend
a single paid call.

| Need | Why | Env var(s) |
| --- | --- | --- |
| **LLM provider** | research planning, extraction, synthesis, creator packet | `REEL_AUTOMATION_DEFAULT_PROVIDER` + that provider's key (e.g. `…_API_KEY`) |
| **Search provider** | source discovery for grounded research | `REEL_AUTOMATION_SEARCH_PROVIDER` + that provider's key |
| **TTS backend** | narration audio | `REEL_AUTOMATION_TTS_BACKEND=kokoro` (local default — no key) + the Kokoro model files |
| **Stock B-roll** | ffmpeg needs ≥1 visual to render | `REEL_AUTOMATION_STOCK_API_KEY` |
| **`ffmpeg` binary** | audio + visuals → MP4 (also provides `ffprobe`) | on your `PATH` |

The default TTS runs **fully locally** (Kokoro on-device — no key, no service):
all the *paid* services have a **free tier with no credit card** (Groq, Tavily,
Pexels). TTS just needs a one-time model download — see
[Step 3](#5-step-3--configure-your-keys-env).

---

## 2. Per-device one-time setup

Do this once per machine.

```bash
# 1. Clone
git clone <your-fork-or-this-repo-url> reel-automation
cd reel-automation

# 2. Python 3.11+ on PATH (verify)
python3 --version          # must be 3.11 or newer

# 3. ffmpeg (provides both ffmpeg and ffprobe)
brew install ffmpeg        # macOS
# Debian/Ubuntu:  sudo apt-get update && sudo apt-get install -y ffmpeg
ffmpeg -version            # verify it's on PATH
```

All `make` commands below run from the **repository root**.

---

## 3. Step 1 — install the backend (`make setup`)

```bash
make setup
```

This creates a project-local virtualenv at `backend/.venv`, upgrades `pip`, and
installs the backend in editable mode with dev extras
(`pip install -e "./backend[dev]"`). See [`operations.md`](operations.md#running-locally-makefile--venv)
for the full set of `make` targets.

---

## 4. Step 2 — hermetic sanity check (`make check`)

Before touching any keys, prove the install is healthy. `make check` runs the
exact CI gates (lint, format-check, type-check, tests) — **fully offline, no keys,
no network**:

```bash
make check
```

The whole Deep Research + Media pipeline is exercised here with fake providers,
so a green `make check` means the code is sound on your machine before you spend
a real call. If this fails, fix it before continuing (it points at an install
problem, not a config problem).

---

## 5. Step 3 — configure your keys (`.env`)

```bash
cp backend/.env.example backend/.env
# then edit backend/.env
```

`backend/.env` is gitignored and loaded relative to the process working
directory, so the pipeline commands run **from `backend/`** (the `make` targets
already do this for you). Get the four services:

### LLM — Groq (free, no card)
Sign up at <https://console.groq.com>, create an API key (`gsk_…`), and paste it
into **both** `REEL_AUTOMATION_API_KEY` and `REEL_AUTOMATION_GROQ_API_KEY` in the
template (the template defaults `DEFAULT_PROVIDER=openai-compatible` pointing at
Groq's base URL). Verify the current model ids at
<https://console.groq.com/docs/models>. Any OpenAI-compatible backend works by
swapping `BASE_URL` + `API_KEY` + model ids — see
[`configuration.md`](configuration.md#llm-providers).

### Search — Tavily (free tier, no card)
Get a key (`tvly-…`) at <https://app.tavily.com> and paste it into
`REEL_AUTOMATION_SEARCH_API_KEY` (the template defaults `SEARCH_PROVIDER=tavily`).
Brave Search is the alternative (`SEARCH_PROVIDER=brave` + `BRAVE_API_KEY`).

### Stock B-roll — Pexels (free, no card)
Get a key at <https://www.pexels.com/api/> and paste it into
`REEL_AUTOMATION_STOCK_API_KEY`. Required — ffmpeg needs at least one visual.

### TTS — local Kokoro (default, no service)
TTS is a **supervised router** over a local-first fabric (ADR 0050). The default
backend, **Kokoro**, runs the Apache-2.0 Kokoro-82M model entirely on your machine
via ONNX Runtime — no TTS service, no key, no per-call cost. One-time setup:

```bash
cd backend && .venv/bin/pip install kokoro-onnx
# Download both files from the kokoro-onnx releases and point the paths at them:
#   https://github.com/thewh1teagle/kokoro-onnx/releases
#   - kokoro-v1.0.onnx   -> REEL_AUTOMATION_KOKORO_MODEL_PATH
#   - voices-v1.0.bin    -> REEL_AUTOMATION_KOKORO_VOICES_PATH
```

Set `REEL_AUTOMATION_TTS_VOICE` to a Kokoro voice id (default `af_heart`).
Optional NVIDIA/HuggingFace fallbacks join the router only if you set their key
(`REEL_AUTOMATION_NVIDIA_TTS_API_KEY` / `REEL_AUTOMATION_HUGGINGFACE_TTS_API_KEY`);
the contract is documented in `backend/.env.example` and ADRs 0046-0050.

---

## 6. Step 4 — preflight (`make doctor`)

Before spending a paid call, run the preflight. It checks readiness **without any
network calls** and prints a ✓/✗ table naming the exact env var or binary to fix:

```bash
make doctor
```

It verifies: the configured LLM provider's required key/base_url, the search
provider's key, the TTS backend (for `kokoro`: the `kokoro-onnx` package + the
model/voices files; for `nvidia`/`huggingface`: that backend's key), the stock
key, `ffmpeg` **and** `ffprobe` on PATH, and that the output directory exists or
can be created. It exits non-zero if
any hard requirement is missing — so a green `make doctor` means `make video`
won't fail for a config reason. (Run it directly if you prefer:
`cd backend && .venv/bin/python -m app.cli.doctor`.)

---

## 7. Step 5 — make your first video (`make video`)

```bash
make video TOPIC="why fusion ignition is hard"
```

This runs the full linchpin pipeline —
`topic → research → creator packet → media → finished video` — and prints the
resulting `VideoArtifact` as JSON. The `video_uri` field points at the rendered
file:

```
backend/renders/vid_<hex>.mp4
```

That MP4 is your first video. (Omit `TOPIC=` and it defaults to "the James Webb
Space Telescope".) The render writes audio + visual blobs and the final MP4 into
`REEL_AUTOMATION_MEDIA_OUTPUT_DIR` (default `backend/renders`).

---

## 8. Troubleshooting

If `make video` fails, the message is almost always a `CompositionError` naming
the missing piece. `make doctor` catches all of these up front; this maps each
real error to its fix:

| Error message (from `composition.py`) | Fix |
| --- | --- |
| `default_provider='openai-compatible' requires REEL_AUTOMATION_BASE_URL` | set `REEL_AUTOMATION_BASE_URL` |
| `default_provider='openai-compatible' requires REEL_AUTOMATION_API_KEY` | set `REEL_AUTOMATION_API_KEY` |
| `default_provider='gemini' requires REEL_AUTOMATION_GEMINI_API_KEY` | set `REEL_AUTOMATION_GEMINI_API_KEY` |
| `provider preset '…' requires an API key` | set that preset's `REEL_AUTOMATION_<NAME>_API_KEY` |
| `no model provider adapter for default_provider=…` | set `REEL_AUTOMATION_DEFAULT_PROVIDER` to `openai-compatible`, `gemini`, `groq`, `nvidia`, `huggingface`, or `ollama` |
| `search_provider='tavily' requires REEL_AUTOMATION_SEARCH_API_KEY` | set `REEL_AUTOMATION_SEARCH_API_KEY` |
| `search_provider='brave' requires REEL_AUTOMATION_BRAVE_API_KEY` | set `REEL_AUTOMATION_BRAVE_API_KEY` |
| `no search provider adapter for search_provider=…` | set `REEL_AUTOMATION_SEARCH_PROVIDER` to `tavily` or `brave` |
| TTS: `kokoro-onnx` not installed / model files missing | `pip install kokoro-onnx` + download the model + voices files (see Step 3) |

Other failure modes:

- **`ffmpeg`/`ffprobe` not found** — install ffmpeg (`brew install ffmpeg`) and
  re-run `make doctor`.
- **Render fails with no visual / empty visuals** — set
  `REEL_AUTOMATION_STOCK_API_KEY` (Pexels); ffmpeg needs ≥1 visual. `make doctor`
  flags this as a hard ✗.
- **401 / 404 at the first model call** — your key or model id is wrong; verify
  the current model ids at your provider (Groq's are at
  <https://console.groq.com/docs/models>).
- **`.env` not picked up** — it must be `backend/.env`; the `make` targets run
  from `backend/`. Running the CLI by hand requires `cd backend` first.

---

## 9. What this does and does not do yet

**Built and reachable from `make video`:** the full
`topic → research → creator packet → media → MP4` pipeline (ADR 0032), with live
LLM / search / TTS / stock-visual providers selected by config.

**Not yet wired (documented gaps):**

- **No distribution or analytics.** Posting to platforms, scheduling, and the
  analytics feedback loop are forward-looking layers (CLAUDE.md §3.4). The
  pre-publish safety gate and SEO/thumbnail builders exist as tools but are not
  chained into an auto-publish flow.
- **Single-process, in-memory job model.** The synchronous CLI holds state for
  the run; there is no durable job store
  ([operations.md](operations.md#single-process-in-memory-job-model-no-job-store)).
- **Ingestion coverage.** Research ingestion handles **WEB** (HTML) and **PDF**
  (text layer). YouTube has an adapter but it is not wired; scanned/image-PDF OCR
  is unsupported ([configuration.md](configuration.md#ingestion-providers)).

For the build sequence and the broader vision see [`docs/ROADMAP.md`](ROADMAP.md)
and [`docs/product-vision.md`](product-vision.md).
```
