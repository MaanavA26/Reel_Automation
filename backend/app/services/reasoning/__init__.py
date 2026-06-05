"""Knowledge Reasoning band services — deterministic helpers (no LLM).

Per CLAUDE.md §4 this package holds the *tool/service* half of the reasoning
band: deterministic, repeatable transforms the band's agents call. The agents
themselves (judgment) live under ``app/agents/``. The first member is
`claim_blocking`, which groups evidence into candidate clusters so the
Cross-Verification agent (M8) judges bounded, related sets rather than the full
O(N²) cross-product. See ADR 0010.
"""
