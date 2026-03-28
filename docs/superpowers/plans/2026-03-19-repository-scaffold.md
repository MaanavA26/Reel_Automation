# Reel Automation Repository Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the initial production-grade repository scaffold for Reel Automation with a minimal FastAPI backend, a minimal React frontend, shared documentation structure, Deep Research placeholders, and focused tests.

**Architecture:** The scaffold should preserve clean boundaries described in `AGENTS.md` and `ARCHITECTURE.md`: thin API modules, typed schemas, service and workflow separation, and React UI code isolated from service contracts. The first pass should establish only the repository shape, bootstrap entrypoints, and placeholders required for future work, without speculative business logic or early LangGraph implementation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest, React 18, TypeScript, Vite

---

## Planned File Structure

- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/router.py`
- Create: `backend/app/api/health.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/health.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/deep_research.py`
- Create: `backend/app/agents/__init__.py`
- Create: `backend/app/agents/deep_research.py`
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/deep_research.py`
- Create: `backend/app/workflows/__init__.py`
- Create: `backend/app/workflows/deep_research.py`
- Create: `backend/tests/test_health.py`
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/pages/HomePage.tsx`
- Create: `frontend/src/services/api.ts`
- Create: `frontend/src/types/health.ts`
- Create: `docs/adrs/README.md`
- Create: `docs/standards/README.md`
- Modify: `README.md`

### Task 1: Establish Documentation And Repository Layout

**Files:**
- Create: `docs/adrs/README.md`
- Create: `docs/standards/README.md`
- Modify: `README.md`

- [ ] **Step 1: Write the failing documentation expectation**

```text
Expect README.md to describe backend, frontend, docs, and tests directories.
Expect docs/adrs and docs/standards to exist with short purpose statements.
```

- [ ] **Step 2: Run a file check to verify the structure is missing**

Run: `find docs -maxdepth 2 -type f | sort`
Expected: missing `docs/adrs/README.md` and `docs/standards/README.md`

- [ ] **Step 3: Write the minimal documentation structure**

```md
# Repository Layout

- backend/: FastAPI application and tests
- frontend/: React application
- docs/adrs/: architecture decision records
- docs/standards/: shared engineering standards
```

- [ ] **Step 4: Verify the files now exist**

Run: `find docs -maxdepth 2 -type f | sort`
Expected: includes `docs/adrs/README.md` and `docs/standards/README.md`

- [ ] **Step 5: Commit**

```bash
git add README.md docs/adrs/README.md docs/standards/README.md
git commit -m "docs: add repository scaffold documentation"
```

### Task 2: Add Minimal FastAPI Backend Skeleton

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/router.py`
- Create: `backend/app/api/health.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/health.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/deep_research.py`
- Create: `backend/app/agents/__init__.py`
- Create: `backend/app/agents/deep_research.py`
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/deep_research.py`
- Create: `backend/app/workflows/__init__.py`
- Create: `backend/app/workflows/deep_research.py`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing backend test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "reel-automation"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_health.py -v`
Expected: FAIL because the backend app files do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: add backend application scaffold"
```

### Task 3: Add Minimal React Frontend Skeleton

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/pages/HomePage.tsx`
- Create: `frontend/src/services/api.ts`
- Create: `frontend/src/types/health.ts`

- [ ] **Step 1: Write the failing frontend expectation**

```text
Expect the frontend to expose a typed React entrypoint, a page shell, and a service module for future API calls.
```

- [ ] **Step 2: Run a file check to verify the structure is missing**

Run: `find frontend -maxdepth 3 -type f | sort`
Expected: frontend scaffold files are absent

- [ ] **Step 3: Write the minimal implementation**

```tsx
export function AppShell(): JSX.Element {
  return (
    <div className="app-shell">
      <main>
        <HomePage />
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Run a minimal validation**

Run: `find frontend -maxdepth 3 -type f | sort`
Expected: includes `frontend/src/App.tsx`, `frontend/src/components/AppShell.tsx`, and `frontend/src/pages/HomePage.tsx`

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: add frontend application scaffold"
```

### Task 4: Run Minimal Repository Validation

**Files:**
- Modify: `README.md`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: Run targeted backend validation**

Run: `python3 -m compileall backend/app backend/tests`
Expected: PASS with compiled backend modules

- [ ] **Step 2: Run structure validation**

Run: `find backend frontend docs -maxdepth 3 -type f | sort`
Expected: shows the new scaffold files across backend, frontend, and docs

- [ ] **Step 3: Record any missing environment prerequisites**

```text
If FastAPI, pytest, or frontend dependencies are not installed, record that only syntax/structure validation was run in this scaffold pass.
```

- [ ] **Step 4: Summarize the implementation outcome**

```text
Document the final repository layout, health endpoint, React shell, and placeholder Deep Research modules.
```

- [ ] **Step 5: Commit**

```bash
git add README.md backend frontend docs
git commit -m "chore: validate initial repository scaffold"
```
