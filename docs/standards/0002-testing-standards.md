# Standard 0002: Testing Standards

- **Status:** Accepted
- **Date:** 2026-05-25
- **Deciders:** Tech Lead, Council (advisor + `Maanav's-MacAir`)
- **Supersedes:** none
- **Superseded by:** none

This document is **normative** — every test change follows it. The [Rules](#rules) section is what contributors need at test-writing time. The [Rationale](#rationale) section is reference for *why* each rule exists.

---

## Rules

### Backend test layout

Tests live in `backend/tests/`, mirroring the source structure under `backend/app/`:

- Unit tests for `backend/app/<area>/<module>.py` live at `backend/tests/<area>/test_<module>.py`.
- Tests that span subsystems live under `backend/tests/integration/` and are marked with `@pytest.mark.integration`.
- Test-only fixtures shared across files live in `backend/tests/conftest.py` (or scoped `conftest.py` files for narrower fixtures).

### Test file naming

Test files follow the pattern `test_<module-under-test>.py`, mirroring the source module name. Examples:

- `app/services/research.py` → `backend/tests/services/test_research.py`.
- `app/agents/orchestrator.py` → `backend/tests/agents/test_orchestrator.py`.

Test functions are `test_<behavior>`, named for the behavior verified, not the function called. Prefer `test_health_endpoint_returns_ok` over `test_get_health`.

### Discovery and config

Pytest discovery is configured in `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Tests run with `cd backend && pytest`. CI runs the same command from `working-directory: backend`. Do not add per-test or per-file path manipulation; the standard config must be sufficient.

### Frontend tests

**Non-binding intent.** When frontend tests are added, the default *proposal* is `vitest` + `@testing-library/react`. This is **not** enforceable today — the binding decision lives in a future testing-standards ADR written at the time tests actually land. Documenting the intent here prevents the first frontend test PR from picking a different default by accident.

### Coverage

No minimum coverage threshold during Phase 0. Coverage is *measured* (not gated) once it has enough surface to be meaningful — currently 17 mostly-empty source files would produce noise. A target threshold will be set in a future testing-standards ADR once the Deep Research layer has real implementation.

### Test discipline

- Prefer focused unit tests over broad integration tests when a unit boundary exists.
- Add a regression test for every bug fix.
- Validate the smallest relevant scope first (per [CLAUDE.md §9.3](../../CLAUDE.md)).
- Don't mock what you can fake — factories beat mocks.
- Don't mock what you can construct — small Pydantic models beat fixtures.
- Tests that hit external services (LLM providers, databases, network) live behind a marker (`@pytest.mark.integration` or finer-grained) and run in a separate CI job when added.

### Python version matrix

CI runs the backend gate against **`[3.11, 3.12]`**.

- `3.13` is deferred to a future polish PR. The scaffold's empty placeholders don't surface 3.13-specific behavior (typing changes, async refinements); validating against 3.13 will be meaningful once Deep Research code lands.
- The matrix expansion is itself an **implementation follow-up** to this standard, tracked in [issue #5](https://github.com/MaanavA26/Reel_Automation/issues/5). This standard sets the rule; the workflow change is a separate PR.

**Decides [issue #5 item 3](https://github.com/MaanavA26/Reel_Automation/issues/5); implementation in a follow-up PR.**

### Node version and `@types/node` alignment

CI runs Node 20 LTS. `frontend/package.json`'s `@types/node` major version must match the Node runtime major (`^20`).

- Current state (as of this ADR): misaligned at `@types/node@^25.8.0`. The bump-to-`^20` is an **implementation follow-up** tracked in [issue #5](https://github.com/MaanavA26/Reel_Automation/issues/5).
- Future Node major bumps move CI runtime and `@types/node` together, in the same PR.

**Decides [issue #5 item 1](https://github.com/MaanavA26/Reel_Automation/issues/5); implementation in a follow-up PR.**

---

## Rationale

### Status / context

The repository has one test today (`backend/tests/test_health.py`). Before more tests land, the layout, naming convention, frontend framework intent, and CI matrix need to be written down — otherwise the first three test PRs accidentally set divergent precedents that are painful to undo.

PR #4's review also flagged two CI-matrix questions that belong in a testing-standards ADR: Python version coverage (single-version vs. matrix) and `@types/node` alignment with the Node runtime major.

### Decision

The rules above are the standard. Three explicit decisions worth highlighting:

**1. `[3.11, 3.12]` Python matrix now; defer `3.13`.** Single-version CI is a credibility gap for a public-showcase production-grade project: anyone evaluating the repo reads `python_requires=">=3.11"` and expects ≥2 versions in the matrix. `3.12` is stable enough for the scaffold; `3.13` introduces typing/async changes that would benefit from real code to validate against. Two-version cost on a 19-second backend job rounds to seconds, and CI runners are free for public repos.

**2. `@types/node` major must match Node runtime major.** PR #4 hit a real failure rooted in this exact misalignment. The rule prevents the failure mode in general, not just for Node 20. When Node CI bumps to 22 (or 24, the deprecation pressure flagged on PR #4), `@types/node` follows in the same PR.

**3. Vitest as the future frontend test default — declared, not enforced.** No frontend tests exist today, so there's nothing to enforce. Declaring the intent prevents the first frontend test PR from defaulting to Jest by accident. The binding decision will be made by a dedicated ADR when tests actually arrive.

### Consequences

**Positive.**
- Two-version Python matrix matches "production-grade" expectations and catches version-specific bugs early.
- `@types/node` alignment prevents the kind of CI failure PR #4 hit.
- Test layout decided before drift starts; the first ten test files won't disagree on naming.
- Frontend test framework intent documented so the eventual ADR can be a small ratification rather than a contentious decision.

**Negative.**
- ~2× CI runner time on the backend job once the matrix expands. Acceptable on a sub-30-second job; free on public-repo runners.
- Vitest "intent" could be wrong if the React testing ecosystem shifts before tests land. Mitigated: the future ADR is binding; this standard is non-binding intent.

### Alternatives considered

- **Single 3.11 forever.** Rejected: credibility gap for a showcase repo; doesn't catch version-specific regressions.
- **`[3.11, 3.12, 3.13]` now.** Rejected: 3.13 doesn't yield meaningful signal on placeholder code; defer until Deep Research lands.
- **`@types/node@latest` (no pin).** Rejected: drifts on every major release; major-alignment-to-runtime is the stable rule.
- **Pick the frontend test framework now (Jest or Vitest) and wire it.** Rejected: there's nothing to test today; wiring without tests adds dependencies without value.
- **Hard coverage threshold from day one (e.g., 80%).** Rejected: too noisy on 17 mostly-empty source files; would force test-of-test-existence rather than meaningful coverage.

### References

- [CLAUDE.md §9.3](../../CLAUDE.md) — testing rules.
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — PR checklist requires tests for behavior changes.
- [PR #4 review verdict — non-blocking follow-ups](https://github.com/MaanavA26/Reel_Automation/pull/4#issuecomment-4529093528) — origin of the Python matrix and `@types/node` items.
- [PR #6 review verdict](https://github.com/MaanavA26/Reel_Automation/pull/6#issuecomment-4529694676) — confirms deferred-checks sequencing aligns with this standards approach.
- [Issue #5](https://github.com/MaanavA26/Reel_Automation/issues/5) — tracked follow-ups (this standard resolves items 1 and 3; item 2 is in [Standard 0001](./0001-coding-standards.md)).
- Related: [Standard 0001 — Coding Standards](./0001-coding-standards.md).
