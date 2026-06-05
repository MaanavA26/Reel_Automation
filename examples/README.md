# Reel Automation — client examples

Runnable, illustrative scripts that drive the Reel Automation Deep Research API
through the typed Python client (`backend/app/client`). They are **not** run in
CI — they require a live server and a wired production search/model adapter.

## Prerequisites

1. Install the backend (from `backend/`):

   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e .
   ```

2. Start the API locally (from `backend/`):

   ```bash
   uvicorn app.main:app --reload
   ```

   The server listens on `http://localhost:8000` by default; the examples point
   there. Override with the `REEL_AUTOMATION_BASE_URL` environment variable.

3. Run an example **from the `backend/` directory** (so `app` is importable),
   pointing Python at the repo's `examples/`:

   ```bash
   # from backend/
   python ../examples/health_check.py
   python ../examples/sync_research.py "the cognition of octopuses"
   python ../examples/async_research_poll.py "the cognition of octopuses"
   ```

## Scripts

| Script | Endpoint(s) | Shows |
| --- | --- | --- |
| `health_check.py` | `GET /health` | Liveness probe + typed `HealthResponse`. |
| `sync_research.py` | `POST /research` | One blocking call → terminal `ResearchState`. |
| `async_research_poll.py` | `POST /research/jobs`, `GET /research/jobs/{id}` | Enqueue then poll to completion. |

## Note on the default `POST /research` adapter

Out of the box the workflow has no production search/model adapter wired, so the
research endpoints return **503**. The examples surface that cleanly via the
client's typed `ReelAutomationAPIError`. Wire a real provider (see
`docs/operations.md`) to get a populated `ResearchState`.
