<!--
Thank you for contributing. Please fill out every section. PRs missing the
checklist or the test plan will be sent back for revision.

For the operating contract, see CONTRIBUTING.md.
-->

## Summary

<!-- One or two sentences: what is this PR doing and for whom? -->

## Problem / context

<!-- What problem motivates this change? Link to issue, ADR, or design doc if applicable. -->

## Approach

<!-- How does this PR solve the problem? Call out key design choices and the main tradeoff considered. -->

## Linked artifacts

- ADR: <!-- e.g., docs/adrs/0001-research-state.md, or "n/a" -->
- Design doc: <!-- link or "n/a" -->
- Issue: <!-- closes #N, or "n/a" -->

## Test plan

<!--
How was this change tested? Cover the golden path and at least one edge case.
For UI changes, describe what you exercised in a browser.
-->

- [ ] Unit tests cover the new behavior
- [ ] Integration tests cover cross-component effects (or marked n/a with reason)
- [ ] Manual verification (if applicable): describe steps and expected result

## Checklist

- [ ] **Tests** — unit tests added or updated; integration tests added if cross-component
- [ ] **Docs** — README, module docstrings, or design doc updated (yes/no + reason below)
- [ ] **ADR** — architectural decision recorded in `docs/adrs/` (yes/no + reason below)
- [ ] **Release notes** — `CHANGELOG.md` updated if user- or API-facing (yes/no + reason below)
- [ ] **Backward compatibility** — considered; any breaking change is explicitly called out and justified
- [ ] **Pre-commit checks pass** — lint, type-check, tests (locally, before pushing)
- [ ] **Branch hygiene** — branched from `main`, conventional commit messages, `Co-authored-by` trailer on AI-assisted commits
- [ ] **Council review** — advisor or reviewer second opinion captured (link in comments) for non-trivial changes
- [ ] **Scope discipline** — only files necessary for this PR are touched

### Checklist notes

- **Docs:** <!-- yes/no — reason -->
- **ADR:** <!-- yes/no — reason -->
- **Release notes:** <!-- yes/no — reason -->
- **Backward compat:** <!-- summary of impact, or "n/a" -->

## Risks and follow-ups

<!--
What could break? What is intentionally left out and tracked as a follow-up?
Drop links to follow-up issues here.
-->
