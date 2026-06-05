# syntax=docker/dockerfile:1
#
# Reel Automation — backend image (FastAPI + uvicorn).
#
# Multi-stage build:
#   1. builder  — installs the application into an isolated virtualenv from
#                 pyproject.toml (no dev extras, no editable install).
#   2. runtime  — slim base, copies only the populated venv + app source,
#                 runs as a non-root user, and self-checks /api/v1/health.
#
# Build context is `backend/`. Only pyproject.toml + app/ are copied; tests/
# and local caches are excluded via .dockerignore to keep the image lean and
# never bake the gitignored .env into a layer.

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# Deterministic, quiet Python; no .pyc litter; unbuffered logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Isolated virtualenv so the runtime stage can copy a self-contained tree.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the install needs to maximize layer caching and keep the
# build context minimal. `app/` must be present for the package build.
COPY pyproject.toml ./
COPY app ./app

# Install runtime dependencies + the application (non-editable, no dev extras).
RUN python -m pip install --upgrade pip \
    && python -m pip install .

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user (defense-in-depth; never run app processes as root).
RUN groupadd --system app && useradd --system --gid app --no-create-home appuser

# Copy the populated virtualenv from the builder. The `app` package is already
# installed into the venv's site-packages by `pip install .`, so uvicorn
# imports it from there — no separate source COPY needed.
COPY --from=builder /opt/venv /opt/venv

USER appuser

EXPOSE 8000

# In-container health probe (slim base has no curl). Targets the real route on
# loopback; a non-200 or connection error exits non-zero -> "unhealthy".
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"]

# Bind to all interfaces so the container is reachable from the host / compose
# network. The healthcheck above still probes loopback from inside.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
