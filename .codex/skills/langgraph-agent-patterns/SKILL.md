---
name: langgraph-agent-patterns
description: Use this skill when creating or modifying LangGraph workflows, agent nodes, graph state, tool-routing logic, or orchestration code. Do not use for plain utility or UI work.
---

Design rules for LangGraph in this repo:

- Represent long-lived workflow data in a typed state object.
- Keep node responsibilities narrow.
- Separate agents from tools.
- Prefer explicit transitions over hidden branching.
- Add retry or fallback only where justified.
- Emit structured outputs for downstream nodes.
- Avoid agent nodes performing deterministic parsing or formatting.
- Keep tool wrappers thin and testable.
- Preserve provenance and traceability in state where relevant.

When implementing:
1. Define/extend state schema first.
2. Add node contracts second.
3. Add graph wiring third.
4. Add tests for:
   - state transitions
   - happy path
   - one failure path
   - one fallback or loop path if present