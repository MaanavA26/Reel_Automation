# Standards

This directory contains normative engineering standards for Reel Automation. Each standard:

- is numbered and immutable in name (like ADRs);
- applies across backend, frontend, agents, tools, and workflows;
- is consistent with [`CLAUDE.md`](../../CLAUDE.md), [`AGENTS.md`](../../AGENTS.md), [`ARCHITECTURE.md`](../../ARCHITECTURE.md), and any accepted ADRs in [`../adrs/`](../adrs/).

Standards are **normative** — they describe what every PR must follow. ADRs (in [`../adrs/`](../adrs/)) document one-off architectural decisions; standards are the ongoing rules that emerge from those decisions.

## Index

- [Standard 0001 — Coding Standards](./0001-coding-standards.md) — Python and TypeScript conventions, tooling stack, version pinning, secrets handling.
- [Standard 0002 — Testing Standards](./0002-testing-standards.md) — pytest layout, file naming, frontend test framework intent, Python and Node version matrices.
