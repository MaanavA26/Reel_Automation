---
name: repo-conventions
description: Use this skill whenever making any change in this repository. Applies project architecture, file boundaries, coding conventions, and review constraints. Do not trigger for general brainstorming outside the repo.
---

You are working in the Reel Automation repository.

Goals:
- Preserve clean architecture boundaries.
- Make minimal, reviewable changes.
- Follow existing patterns before inventing new ones.
- Prefer typed, modular, production-oriented code.

Repository principles:
- Backend: FastAPI + Pydantic + service-layer boundaries.
- Agent orchestration: LangGraph.
- Frontend: React with clear API abstraction.
- Media/rendering tools are deterministic tools, not agents.
- Reasoning-heavy responsibilities belong to agents.
- Deterministic execution belongs to tools/services.

Rules:
1. Do not modify unrelated files.
2. Do not rename public interfaces unless required.
3. Add or update tests for behavior changes.
4. Prefer small, focused diffs.
5. If architecture changes, update docs and add an ADR.
6. Keep functions/classes reusable and modular.
7. Use clear docstrings on nontrivial public classes/functions.
8. Preserve backward compatibility unless explicitly asked not to.

Before coding:
- Inspect nearby files for style and conventions.
- State which files you plan to change.
- State validation commands before editing.

After coding:
- Summarize changed files.
- Summarize behavioral impact.
- List validation steps run or still required.