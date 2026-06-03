"""Default role->model policy, sourced from application settings.

Keeps routing policy as *configuration data* (CLAUDE.md §6), not hard-coded
selection logic. Model ids default to the current Claude tiers and are
overridable via ``REEL_AUTOMATION_*`` environment variables on `Settings`.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelChoice, RolePolicy


def default_policy(settings: Settings | None = None) -> RolePolicy:
    """Build the default role->model policy from settings.

    Every `ModelRole` is mapped, so the returned policy is complete. The
    provider name is the configured default; no provider need be *registered*
    for this policy to be constructed — registration and the concrete adapter
    land with the first real consumer (the Research Planner agent, M3).
    """
    s = settings or get_settings()
    return {
        ModelRole.PLANNING: ModelChoice(s.default_provider, s.planning_model),
        ModelRole.EXTRACTION: ModelChoice(s.default_provider, s.extraction_model),
        ModelRole.LONG_CONTEXT: ModelChoice(s.default_provider, s.long_context_model),
        ModelRole.FALLBACK: ModelChoice(s.default_provider, s.fallback_model),
    }
