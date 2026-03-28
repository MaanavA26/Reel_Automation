---
name: test-first-implementation
description: Use this skill when implementing a new backend feature, bugfix, or refactor where behavior can be validated with tests. Do not use for purely cosmetic or documentation-only changes.
---

Workflow:
1. Identify expected behavior.
2. Add or update focused tests first.
3. Implement the smallest change needed to satisfy the tests.
4. Run only relevant tests first.
5. Expand to broader validation only if needed.

Rules:
- Prefer targeted regression tests over broad speculative suites.
- Avoid rewriting unrelated tests.
- Keep fixtures small and readable.
- If behavior is ambiguous, preserve current behavior unless instructed otherwise.

Output format:
- Behavior being validated
- Tests added/updated
- Code changes made
- Validation run
- Remaining risks