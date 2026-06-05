"""Research Publishing band services — deterministic helpers (no LLM).

Per CLAUDE.md §4 this package holds the *tool/service* half of the publishing
band: deterministic transforms the band's `ReportAgent` relies on. `citations`
assembles a source-grounded bibliography by walking the provenance chain;
`caveats` derives the report's non-omittable limitations from the reasoning
state. Both are pure functions — no judgment, no model — so a report's grounding
and caveats are code-owned, never model-authored (the §11 keystone). See
ADR 0017.
"""
