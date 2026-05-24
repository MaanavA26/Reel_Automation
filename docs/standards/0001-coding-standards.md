# Standard 0001: Coding Standards

- **Status:** Accepted
- **Date:** 2026-05-25
- **Deciders:** Tech Lead, Council (advisor + `Maanav's-MacAir`)
- **Supersedes:** none
- **Superseded by:** none

This document is **normative** — every Python and TypeScript change in this repository follows it. The [Rules](#rules) section is what contributors need at coding time. The [Rationale](#rationale) section is reference for *why* each rule exists.

---

## Rules

### Python

**Versions.** Source supports `python >= 3.11`. CI matrix runs `[3.11, 3.12]` (see [Standard 0002](./0002-testing-standards.md)).

**Tooling.** Every Python change must pass, in this order:

1. `ruff check` with rule set: `E`, `F`, `I`, `W`, `B`, `UP`, `RUF`.
2. `ruff format --check`.
3. `mypy` at the moderate strictness baseline: `disallow_untyped_defs`, `warn_unused_ignores`, `warn_redundant_casts`, `warn_unused_configs`.
4. `pytest`.

Configuration lives in `backend/pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.lint.isort]`, `[tool.mypy]`). Do not duplicate configuration into per-module overrides without an ADR.

**Style.**

- Prefer classes and functions over inline scripts. No top-level executable side effects in modules.
- Use `from __future__ import annotations` at module top to allow modern type syntax everywhere.
- `snake_case` for functions, variables, modules; `PascalCase` for classes; `SCREAMING_SNAKE_CASE` for constants.
- Docstrings on public functions and classes when the *why* is non-obvious (per [CLAUDE.md](../../CLAUDE.md)). Never describe *what* the code does when good names already do.
- Avoid premature abstraction. Three similar lines is better than an abstraction added in anticipation of a fourth.

**Architecture.** The agent-vs-tool boundary defined in [CLAUDE.md §4](../../CLAUDE.md) is a hard rule. Before adding a new module, decide:

- Reasoning, judgment, critique, planning, or orchestration? → agent (`backend/app/agents/`).
- Parsing, I/O, deterministic transformation, API wrapper, rendering? → tool or service (`backend/app/tools/` or `backend/app/services/`).
- Cannot decide? Write an ADR before the module.

Mixing agent reasoning logic with deterministic tool code in the same module is not permitted.

### TypeScript / Frontend

**Versions.** Node 20 LTS in CI. `frontend/package.json`'s `@types/node` major version must match the Node runtime major (rule decided in [Standard 0002](./0002-testing-standards.md)).

**Tooling.** Today's frontend gate is `tsc -b` (strict mode is enabled in `tsconfig.json`) + `vite build`. ESLint and Prettier are intentionally **not** wired yet — they're deferred until the frontend grows past ~10 source files or contributor friction shows up. The decision is logged here so the first ESLint/Prettier PR has the trigger condition documented.

**Style.** Follow the conventions Vite + React 18 produce:

- `camelCase` for symbols, function names, variable names.
- `PascalCase` for types, interfaces, React components.
- `kebab-case` for non-component file names; `PascalCase.tsx` for component files.

### Tool version pinning

**Rule: exact-pin everywhere; sync manually in a single PR per bump.**

The same tool version appears in three places by design:

- `.pre-commit-config.yaml` hook `rev:` lines (exact tags).
- `backend/pyproject.toml` `[project.optional-dependencies].dev` (exact versions — `ruff==X.Y.Z`, `mypy==X.Y.Z`).
- `.github/workflows/ci.yml` for tools installed directly in CI (exact versions via env vars — e.g., `GITLEAKS_VERSION: X.Y.Z`).

When bumping a tool, update **all three** sources in the same PR. CI gates, pre-commit hooks, and developers running `pip install -e ".[dev]"` must agree on the version.

**Current state.** As of this standard's date, `backend/pyproject.toml` still uses ranges (`ruff>=0.8,<1.0`, `mypy>=1.13,<2.0`). The bump to exact pins is an implementation follow-up tracked in [issue #5](https://github.com/MaanavA26/Reel_Automation/issues/5). The rule above is normative for all future bumps from that follow-up PR forward.

**Decides [issue #5 item 2](https://github.com/MaanavA26/Reel_Automation/issues/5); implementation in a follow-up PR.** This rule is the floor; if a single-source-of-truth mechanism (`uv` lockfile, constraints file) is introduced later, supersede this standard with a new ADR.

### Secrets handling

- Never hardcode API keys, tokens, credentials, or other secrets in source files, tests, comments, commit messages, markdown examples, or PR descriptions.
- All secrets live in `.env` (gitignored). Tests use environment variables or per-test fixtures; never literals.
- `gitleaks` runs in both pre-commit (staged files) and CI (full repo history). A leaked-secret PR fails before merge and cannot be retroactively cleaned without history rewrite.
- If a secret ever slips through: **rotate the credential first**, then clean history. The leaked value is compromised the moment it hits a public branch, regardless of when it gets removed.

---

## Rationale

### Status / context

PRs #1–#6 wired the tooling stack (ruff, ruff-format, mypy, pytest, gitleaks, actionlint) without an explicit ADR — the meta-decision was deliberately deferred to this standards document. The rationale needs to be captured before more contributors (or AI agents) add code under conventions only implicit in the CI configs.

Two specific issues from PR #4's review (issue #5 items 1 and 2) also resolve here:

- Tool version pinning was inconsistent across `pre-commit` (exact pins) and `pyproject.toml` (ranges).
- Multiple manifestations of the same drift problem (ruff/mypy ranges, gitleaks duplicated across pre-commit and CI) needed one rule, not many.

### Decision

The rules above are the standard. Three explicit decisions worth highlighting:

**1. Moderate mypy strictness, not `strict = true`.** Strict mode against the current scaffold's placeholder `pass` classes is friction without value. The baseline (`disallow_untyped_defs` + a few warns) catches the most common typing mistakes without rejecting legitimate scaffold code. Ratchet up later when the codebase has real logic that benefits.

**2. Exact pins everywhere, manually synced.** Three files updated per tool bump. Alternatives (`uv` lockfile, constraints files, pre-commit-as-source-of-truth) all add tooling weight not justified at Phase 0 scale, or introduce drift between developer-install and CI-install paths. The cost (one PR per tool bump) is low and explicit.

**3. ESLint/Prettier deferred for the frontend.** Today's frontend is a 3-file Vite scaffold. Adding ESLint config now would pin opinions before there's enough code to validate them. Re-evaluate when the frontend hits ~10 source files or when contributor inconsistency shows up.

### Consequences

**Positive.**
- Single rule for tool versions (exact-pin) makes silent drift impossible.
- Mypy moderate floor lets early development move; strict mode is a later ratchet.
- Agent-vs-tool boundary is normative across all new modules.
- Secrets handling is documented in one place — gitleaks is the enforcer, but the cultural rule is here.

**Negative.**
- Bumping a tool requires touching three files. Mitigated by the small number and the rule that the bump is one PR.
- ESLint/Prettier absence may permit small frontend inconsistencies. Acceptable while frontend is a 3-file scaffold; flagged for revisit.

### Alternatives considered

- **Mypy `strict = true` from day one.** Rejected: forces fighting strict against scaffold placeholders. Premature.
- **`uv` lockfile or pip constraints file.** Rejected for Phase 0: adds tooling complexity. Worth revisiting when the dep set explodes (LangGraph, LangChain, provider SDKs).
- **Pre-commit as single source of truth, CI installs hooks instead of `pip install -e ".[dev]"`.** Rejected: developers run `pip install -e ".[dev]"` directly, so pyproject must also be authoritative. Two sources = drift unless we make them agree.
- **Pin only "critical" tools, range the rest.** Rejected: "critical" is subjective; one explicit rule beats a judgment call per tool.
- **Ranges in pyproject + exact in pre-commit (status quo before this standard).** Rejected: the exact mismatch this standard exists to prevent.

### References

- [CLAUDE.md](../../CLAUDE.md) — project operating contract and agent-vs-tool philosophy.
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — branching, commits, review process.
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — layer boundaries.
- [PR #4](https://github.com/MaanavA26/Reel_Automation/pull/4), [PR #6](https://github.com/MaanavA26/Reel_Automation/pull/6) — origin of the tooling stack.
- [Issue #5](https://github.com/MaanavA26/Reel_Automation/issues/5) — tracked tooling follow-ups (this standard resolves item 2; items 1 and 3 are addressed in [Standard 0002](./0002-testing-standards.md)).
- Related: [Standard 0002 — Testing Standards](./0002-testing-standards.md).
