---
name: minimal-change-refactor
description: Use this skill for safe refactors that improve readability, modularity, or maintainability without changing external behavior. Do not use for feature work or architecture redesign.
---

Refactor rules:
- Preserve behavior.
- Preserve public names unless necessary.
- Keep patches small.
- Extract helpers only when reused or meaningfully clarifying.
- Avoid touching unrelated files.
- Add regression coverage if there is any risk of behavioral drift.

Before editing:
- State current behavior to preserve.
- Identify exact files and symbols to change.

After editing:
- Explain why the refactor is behavior-preserving.
- List any renamed internal symbols.
- Confirm validation steps.