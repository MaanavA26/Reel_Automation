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
    d = s.default_provider
    # A per-role provider override (empty => the default provider) tiers the
    # fabric across providers/models by role (#113): e.g. extraction on a local
    # 3B (ollama), the judgment roles on a capable cloud 70B (nvidia).
    return {
        ModelRole.PLANNING: ModelChoice(s.planning_provider or d, s.planning_model),
        ModelRole.EXTRACTION: ModelChoice(s.extraction_provider or d, s.extraction_model),
        ModelRole.LONG_CONTEXT: ModelChoice(s.long_context_provider or d, s.long_context_model),
        ModelRole.FALLBACK: ModelChoice(s.fallback_provider or d, s.fallback_model),
    }
