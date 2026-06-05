# Reel Automation — developer task runner.
#
# These targets reproduce the CI gates (.github/workflows/ci.yml) locally so a
# contributor can verify a change before pushing. Backend gates run from
# backend/ against a project-local virtualenv (backend/.venv); the frontend
# build runs from frontend/ via npm. See CONTRIBUTING.md for the workflow.
#
# Quick start:  make setup && make check

# --- Configuration -----------------------------------------------------------

BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV := $(BACKEND_DIR)/.venv
PY := $(VENV)/bin/python
# Lint/format/type/test binaries live inside the venv once `make setup` runs.
BIN := $(VENV)/bin

# Run a command from inside the backend dir with the venv on PATH. CI uses
# `working-directory: backend`, so mirroring that keeps tool config discovery
# (pyproject.toml) identical to the gate.
BACKEND_RUN := cd $(BACKEND_DIR) && PATH="$(CURDIR)/$(VENV)/bin:$$PATH"

.DEFAULT_GOAL := help

# --- Help --------------------------------------------------------------------

.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Setup -------------------------------------------------------------------

.PHONY: setup
setup: ## Create backend/.venv and install the backend with dev extras.
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "./$(BACKEND_DIR)[dev]"
	@echo "Setup complete. Run 'make check' to reproduce the CI gates."

# --- Backend gates (mirror ci.yml) -------------------------------------------

.PHONY: fmt
fmt: ## Format backend code in place (ruff format).
	$(BACKEND_RUN) && ruff format .

.PHONY: lint
lint: ## Lint backend code (ruff check) — matches CI (no autofix).
	$(BACKEND_RUN) && ruff check .

.PHONY: types
types: ## Type-check the backend (mypy) — uses pyproject packages config.
	$(BACKEND_RUN) && mypy

.PHONY: test
test: ## Run the backend test suite (pytest).
	$(BACKEND_RUN) && pytest

.PHONY: check
check: ## Run all backend gates exactly as CI does (non-mutating).
	$(BACKEND_RUN) && ruff check . && ruff format --check . && mypy && pytest

# --- Frontend ----------------------------------------------------------------

.PHONY: frontend-build
frontend-build: ## Install frontend deps (npm ci) and build (tsc -b && vite build).
	cd $(FRONTEND_DIR) && npm ci && npm run build
