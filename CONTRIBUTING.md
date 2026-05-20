# Contributing to Reel Automation

Reel Automation is a **production-grade, componentized agentic system** for faceless short-form video generation (YouTube Shorts, Instagram Reels, TikTok). It is built deliberately as a public engineering showcase, so the way we work is as important as what we ship.

This document is the operating contract for every change that lands in this repository. Human contributors and AI assistants both follow it.

For the project vision and architecture, read [`CLAUDE.md`](./CLAUDE.md) and [`ARCHITECTURE.md`](./ARCHITECTURE.md) first.

---

## Working principles

1. **Component-first, not MVP-first.** Each component is designed properly, built to production quality, and independently showcaseable before the next one starts.
2. **Agents vs. tools is a hard line.** Reasoning, judgment, critique, orchestration → agent. Parsing, IO, FFmpeg, API wrappers, normalization → tool or service. Never blur them.
3. **Minimal, reviewable diffs.** Change only what the task requires. No unrelated edits, no speculative abstractions, no broad refactors unless explicitly requested.
4. **Architecture decisions are documented.** Anything cross-cutting or non-obvious gets an ADR in [`docs/adrs/`](./docs/adrs/) before or with the implementation PR.
5. **Quality is provable, not asserted.** Every change ships with the tests, docs, and ADRs it needs.

---

## Branching

`main` is protected. **Never commit directly to `main`.** Every change goes through a feature branch and a pull request.

Branch naming:

| Prefix | Use |
|---|---|
| `feat/<phase-or-area>/<slug>` | New feature |
| `fix/<area>/<slug>` | Bug fix |
| `chore/<area>/<slug>` | Tooling, config, non-feature work |
| `docs/<slug>` | Docs-only |
| `adr/<slug>` | Standalone ADR PRs (rare; usually paired with feature) |

Examples: `feat/phase-0/state-schema`, `chore/ci/setup-gates`, `docs/architecture-overview`.

Delete feature branches after merge. Keep the branch list clean.

---

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <imperative summary>`.

| Type | Use |
|---|---|
| `feat` | New user-facing capability |
| `fix` | Bug fix |
| `chore` | Tooling, config, repo hygiene |
| `docs` | Docs-only changes |
| `refactor` | Internal change with no behavioral effect |
| `test` | Test-only changes |
| `perf` | Performance change with no behavioral effect |

Examples:
- `feat(deep-research): add provenance schema`
- `chore(ci): gate PRs on ruff and mypy`
- `docs(adrs): add 0001-research-state`

**AI co-authoring trailer.** Commits produced with AI assistance carry a trailer:

```
Co-authored-by: Maanav's-MacPro <maanav-macpro@local>
```

This is a project convention to mark AI-assisted work transparently. The git author stays the contributor's identity.

Never use `--no-verify` to skip hooks. If a hook fails, fix the underlying issue.

---

## Pull request process

1. **Branch from `main`** with the right prefix.
2. **Implement** in small, reviewable commits.
3. **Self-review** against the PR template checklist before opening.
4. **Open the PR** using [`.github/PULL_REQUEST_TEMPLATE.md`](./.github/PULL_REQUEST_TEMPLATE.md) — fill out every section.
5. **Council review.** At least one second-opinion review (advisor or reviewer) on non-trivial PRs.
6. **CI must pass** (lint, type-check, tests).
7. **Merge.** Squash-merge by default — keeps `main` linear and reviewable, and the squashed commit message becomes the durable record of the change. Preserve merge commits only when a stacked-PR history is intentionally meaningful; call that out in the PR description if so. Delete the branch after merge.
8. **Update `CHANGELOG.md`** if the PR is user- or API-facing.

PRs should be small. If a PR touches more than ~5 logical concerns, split it.

> **Bootstrap-period note (temporary).** CI gates (`ruff`, `mypy`, `pytest`, pre-commit) are scheduled to land in PR #3 of the bootstrap sequence. Until that PR merges, contributors may mark the **"Pre-commit checks pass"** checklist item as **n/a** with reason "CI not yet wired (PR #3)." This note will be removed when CI lands.

---

## Trivial-change bypass

A change qualifies as **trivial** — and may collapse the PR checklist to a single line — if **all** of the following hold:

- ≤10 net lines added or removed (across all files combined)
- Touches only non-architectural Markdown (`*.md` outside `docs/adrs/` and `docs/standards/`) and/or repo metadata (`.gitignore`, `.editorconfig`, `.gitattributes`, and similar)
- Does not change code, schemas, dependencies, CI config, security/permissions, behavior, `LICENSE`, or any file under `docs/adrs/` or `docs/standards/`
- Does not introduce a new file longer than 30 lines

Trivial PRs still require:

- A feature branch (no commits on `main`, ever)
- Conventional commit message + AI co-author trailer
- A PR opened with the template
- **One council reviewer** (a peer or AI reviewer) sanity-checking the diff before merge

The PR description may collapse the full checklist into a single line:

```
Trivial bypass — n/a except branch hygiene and council review.
<one-sentence justification of why this PR qualifies>
```

**Operating-model changes are never trivial.** Adding or modifying the bypass rule itself, or any change to `CONTRIBUTING.md`, `CLAUDE.md`, `AGENTS.md`, or `ARCHITECTURE.md` that alters contributor obligations or architectural direction, always goes through the full ritual.

---

## Architecture Decision Records (ADRs)

Anything that introduces or changes a cross-cutting architectural decision requires an ADR.

- ADRs live in [`docs/adrs/`](./docs/adrs/), numbered (`0001-<slug>.md`).
- Start from [`docs/adrs/0000-adr-template.md`](./docs/adrs/0000-adr-template.md).
- Status moves through `Proposed → Accepted → Superseded`.
- An ADR is part of the same PR as the change it documents, unless the decision is being made standalone before implementation.

If you find yourself making a non-obvious architectural choice without an ADR, stop and write one.

---

## Standards

Engineering standards (coding, testing, observability, etc.) live in [`docs/standards/`](./docs/standards/), numbered like ADRs. Standards are normative — they describe what every PR should follow.

---

## Releases

We follow [Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

- All notable changes go in [`CHANGELOG.md`](./CHANGELOG.md) under `[Unreleased]`.
- When cutting a release, move `[Unreleased]` entries under a new version heading with the release date.
- Bump version in `backend/pyproject.toml` (and `frontend/package.json` once the frontend ships features) to match.

---

## AI involvement disclosure

This project uses AI assistants (Claude Code and similar) as part of the engineering workflow. Decisions go through a **council pattern**: human contributors and AI assistants both consult a second opinion (a reviewer agent or peer) before substantive work lands. The `Co-authored-by` trailer above marks commits produced with AI assistance.

This is intentional. Modern agentic systems are best built by engineers who use them — and the workflow itself is part of the showcase.
