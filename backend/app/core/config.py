"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="REEL_AUTOMATION_",
        extra="ignore",
    )

    app_name: str = "reel-automation"
    api_v1_prefix: str = "/api/v1"

    # Model fabric (CLAUDE.md §6): policy-driven role->model routing. The
    # default provider name and per-role model ids form the default routing
    # policy (see app.services.llm.policy); override via REEL_AUTOMATION_* env.
    default_provider: str = "anthropic"
    planning_model: str = "claude-opus-4-8"
    extraction_model: str = "claude-sonnet-4-6"
    long_context_model: str = "claude-opus-4-8"
    fallback_model: str = "claude-haiku-4-5-20251001"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create a cached settings instance."""
    return Settings()


settings = get_settings()
