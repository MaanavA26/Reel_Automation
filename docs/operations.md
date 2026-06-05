# Operations Guide

A practical operator reference for running and deploying **Reel Automation**:
local development, container deployment, and the known operational limitations
of the current scaffold.

For the full environment-variable and provider configuration reference, see
[`configuration.md`](configuration.md).

> **Scope note.** The repository is in early scaffold / component-build mode
> (CLAUDE.md §13). The backend boots and serves a health endpoint today, but the
> `/research` endpoint is **not** functional out of the box — see
> [Known limitations](#known-limitations) for exactly what is and isn't wired.

---

## Contents

- [Components and ports](#components-and-ports)
- [Running locally (Makefile + .venv)](#running-locally-makefile--venv)
- [Running with Docker / Compose](#running-with-docker--compose)
- [Health checks](#health-checks)
- [Tests](#tests)
- [Known limitations](#known-limitations)

---

## Components and ports

| Component | Stack | Default host port | Health route |
| --- | --- | --- | --- |
| Backend | FastAPI + uvicorn (Python 3.11) | `8000` | `/api/v1/health` |
| Frontend | Vite/React static bundle on nginx | `8080` | `/` |

The API prefix (`/api/v1`) is configurable via `REEL_AUTOMATION_API_V1_PREFIX`
(see [`configuration.md`](configuration.md)).

The browser bundle calls the backend directly via `VITE_API_BASE_URL`, which
defaults to `http://localhost:8000` — no reverse proxy is required for local
development.

---

## Running locally (Makefile + .venv)

The `Makefile` reproduces the CI gates locally against a project-local
virtualenv at `backend/.venv`. All backend targets run from `backend/` so tool
configuration discovery (`pyproject.toml`) matches CI exactly.

### One-time setup

```bash
make setup
```

This creates `backend/.venv`, upgrades `pip`, and installs the backend in
editable mode with dev extras (`pip install -e "./backend[dev]"`). Requires
`python3` (3.11+) on `PATH`.

### Backend gates

| Target | What it runs |
| --- | --- |
| `make fmt` | `ruff format .` (mutating) |
| `make lint` | `ruff check .` (no autofix, matches CI) |
| `make types` | `mypy` |
| `make test` | `pytest` (hermetic; integration tests excluded by default) |
| `make check` | All gates non-mutating, exactly as CI: `ruff check . && ruff format --check . && mypy && pytest` |

### Running the backend server

After `make setup`, run the API from inside `backend/` with the venv active:

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
```

The API serves at `http://localhost:8000`; health is at
`http://localhost:8000/api/v1/health`.

To run against a real LLM provider, copy the env template and fill in a key
before starting the server:

```bash
cp backend/.env.example backend/.env
# edit backend/.env — see configuration.md
```

`backend/.env` is gitignored; `Settings` loads it automatically (the
`env_file=".env"` config is resolved relative to the process working directory,
so start the server from `backend/`).

### Frontend

```bash
make frontend-build      # npm ci && npm run build (from frontend/)
```

For an interactive dev server, run Vite directly from `frontend/` (`npm install`
then `npm run dev`).

---

## Running with Docker / Compose

`docker-compose.yml` brings up both services. The backend image is a multi-stage
build (builder venv → slim runtime, non-root `appuser`); the frontend image
builds the static bundle and serves it from nginx with an SPA history fallback.

### Quick start

```bash
cp backend/.env.example backend/.env   # provide provider key(s)
docker compose up --build
```

Endpoints on the host:

- Backend → `http://localhost:8000` (health: `/api/v1/health`)
- Frontend → `http://localhost:8080`

### How configuration flows into the containers

- **Backend.** Compose mounts `backend/.env` via `env_file`. The `.env` is
  gitignored and never baked into an image layer (`.dockerignore` excludes it,
  and the Dockerfile copies only `pyproject.toml` + `app/`). Provider keys and
  model routing therefore live only in your local `.env`.
- **Frontend.** The browser-side API base is baked at **build time** through the
  `VITE_API_BASE_URL` build arg (Vite inlines `VITE_*` vars). It defaults to
  `http://localhost:8000`. To point the bundle elsewhere, set it before build:

  ```bash
  VITE_API_BASE_URL=https://api.example.com docker compose build frontend
  ```

The frontend waits for the backend to report healthy (`depends_on … condition:
service_healthy`) before starting. Both services use `restart: unless-stopped`.

### Image build in CI

The `Docker Build` workflow (`.github/workflows/docker-build.yml`) is
**build-only** — it builds both images on pushes/PRs to `main` to catch
Dockerfile regressions. It does not run, publish, or tag images to a registry.

---

## Health checks

Both the Dockerfiles and Compose define container health probes:

- **Backend** — an in-container Python probe (`urllib.request`) against
  `http://127.0.0.1:8000/api/v1/health`. A non-200 or connection error marks the
  container unhealthy. (`30s` interval, `5s` timeout, `20s` start period, 3
  retries.)
- **Frontend** — a BusyBox `wget` spider against `http://127.0.0.1:80/`. (`30s`
  interval, `5s` timeout, `10s` start period, 3 retries.)

`/api/v1/health` is the single readiness signal for the backend and works
regardless of provider configuration.

---

## Tests

The default `pytest` run is **hermetic** — it never touches the network or
requires credentials. Tests requiring live external services are marked
`@pytest.mark.integration` and excluded by default (`addopts = "-m 'not
integration'"`).

To run the network-gated / credentialed integration tests (provider smoke
tests, etc.), supply credentials in the environment and opt in:

```bash
cd backend
.venv/bin/pytest -m integration
```

These exercise the live LLM and search adapters via real HTTP calls and so
require network access plus a valid key for the provider under test.

---

## Known limitations

These are current, intentional constraints of the scaffold. They are stated here
so an operator is not surprised — none are bugs.

### `/research` is not functional out of the box

The API boots and `/api/v1/health` responds, but the `POST /research` endpoint
returns **HTTP 503** under the default configuration. There are two independent
wiring holes, each surfaced as a loud `CompositionError` (mapped to 503) rather
than a silent stub:

1. **No model adapter for the default provider.** `Settings.default_provider`
   defaults to `"anthropic"`, but the router factory
   (`app/services/llm/factory.py`) only wires the **OpenAI-compatible** adapter.
   With the default value, `build_router_from_settings` raises. The minimum fix
   is to set `REEL_AUTOMATION_DEFAULT_PROVIDER=openai-compatible` plus a
   `base_url`, `api_key`, and model ids (this is exactly what `backend/.env.example`
   does). See [`configuration.md`](configuration.md).
2. **No production search provider is wired.** The composition root's
   `_build_search_provider` unconditionally raises `CompositionError`. The live
   Tavily and Brave adapters exist as code but are **not** connected to the
   composition root, and there is **no environment-only way to activate search
   today**. This hole has no `.env` fix in the current scaffold.

Because of (2), even a fully configured model provider is not enough to run a
research job end-to-end through the HTTP API today. End-to-end runs are
exercised in tests by injecting a fake-backed `ResearchDeps` bundle and
overriding the FastAPI dependency.

### Single-process, in-memory job model (no job store)

`POST /research` runs the job **synchronously** and returns the terminal
`ResearchState` as the response body — it doubles as both "submit" and "read
result". There is **no job store**: no background execution, no streaming
progress, and no id-addressable `GET /research/{id}` status endpoint. State
lives only for the duration of the request, in the single API process.
Consequences for operators:

- A research run holds an HTTP connection open for its full duration.
- Horizontal scaling does not share job state — there is nothing to share yet.
- Restarting the process loses any in-flight work.

Background execution and a persistent job store are deferred (ADR 0016).

### Network-gated live tests and offline-build caveats

- **Integration tests need network + credentials.** The hermetic default run
  covers all adapters offline (via `httpx.MockTransport` and fakes); the live
  paths are only validated under `-m integration` with real keys and network.
- **The `youtube` extra is network-gated and optional.** YouTube transcript
  ingestion depends on the optional `youtube-transcript-api` package
  (`pip install -e "./backend[youtube]"`). It is lazy-imported so the package
  imports cleanly without it. The dependency is not installed in the offline
  build sandbox, and `pypdf` is likewise treated as possibly-absent there
  (handled via mypy overrides). When absent, the affected source types fail per
  source (skip + log) rather than crashing.

### Ingestion coverage

The wired ingestion service handles **WEB** (HTML) and **PDF** (text layer)
sources. **YouTube** ingestion has an adapter (`YouTubeTranscriptProvider`) but
the composition root constructs the service without a transcript provider, so
YouTube sources are currently **skipped**. Scanned/image PDFs (OCR) are not yet
supported. See [`configuration.md`](configuration.md#ingestion-providers) for
the provider matrix.
