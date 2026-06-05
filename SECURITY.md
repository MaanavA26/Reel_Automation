# Security Policy

Reel Automation is an early-stage research and agentic-systems project, built
in the open as an engineering showcase. We take the security of the codebase
and its users seriously and welcome responsible disclosure.

## Supported scope

The project is under active development and has not yet cut a stable release
line. Security review and fixes target the latest `main` branch. Older commits
and unreleased feature branches are out of scope.

In scope:

- The backend service (`backend/`) and its workflow, agent, and service code.
- The frontend shell (`frontend/`).
- Repository configuration, CI workflows, and supply-chain hygiene.

Out of scope:

- Third-party LLM providers, search providers, and other external services
  reached through configurable adapters.
- Issues that require committed secrets or credentials we do not ship (see
  below — we never commit secrets).

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately via **GitHub private vulnerability reporting** — use the
"Report a vulnerability" button under the repository's **Security** tab. This
opens a private advisory visible only to maintainers.

Please include a clear description, affected paths or components, reproduction
steps, and impact assessment where possible.

## What to expect

- Acknowledgement of your report within a few business days.
- An initial assessment and severity triage shortly after.
- Coordinated disclosure once a fix or mitigation is available; we are happy to
  credit reporters who wish to be named.

## Secrets

Secrets must **never** be committed to this repository. API keys, tokens, and
credentials belong in local, gitignored `.env` files (see `.env.example`).
Automated **gitleaks** scanning and GitHub **push protection** enforce this; if
either flags a leaked secret, treat it as exposed and rotate it immediately.
