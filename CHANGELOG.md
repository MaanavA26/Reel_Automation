# Changelog

All notable changes to Reel Automation are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries under `[Unreleased]` describe changes that have landed on `main` but
are not yet part of a tagged release. When cutting a release, move the
`[Unreleased]` section under a new version heading with the release date.

## [Unreleased]

## [0.1.0] - 2026-03-29

### Added

- Initial Reel Automation scaffold.
- FastAPI backend application bootstrap with a versioned health endpoint at `/api/v1/health`.
- Typed configuration layer via `pydantic-settings` under `backend/app/core/`.
- Module boundaries for the future Deep Research layer: `backend/app/agents/`, `backend/app/services/`, `backend/app/tools/`, `backend/app/workflows/` (placeholders).
- React + Vite + TypeScript frontend shell with API service abstraction and a home page.
- Operating contracts: `CLAUDE.md`, `ARCHITECTURE.md`, `AGENTS.md`, `README.md`.
- Empty placeholders for `docs/adrs/` and `docs/standards/`.
- Backend test scaffold with a passing health-endpoint test.
