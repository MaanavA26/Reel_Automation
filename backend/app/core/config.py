"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="REEL_AUTOMATION_",
        env_file=".env",
        env_file_encoding="utf-8",
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

    # Provider connection (used by the OpenAI-compatible adapter). Empty by
    # default; set via .env / env vars for live use. `api_key` is a SecretStr so
    # it never leaks into logs or reprs. See .env.example.
    base_url: str = ""
    api_key: SecretStr = SecretStr("")

    # Search fabric (used by the live `SearchProvider` adapter; CLAUDE.md §4,
    # ADR 0013). Kept separate from the LLM `api_key`/`base_url` above so an
    # operator can configure search and the model independently. `search_api_key`
    # is a SecretStr so it never leaks into logs or reprs. Empty by default; set
    # via .env / env vars for live use. See .env.example.
    search_api_key: SecretStr = SecretStr("")

    # Search fabric (used by the live `SearchProvider` adapters; CLAUDE.md §4).
    # `brave_api_key` configures the Brave Web Search adapter (ADR 0021) and is
    # kept distinct from the LLM `api_key` (and from any other search provider's
    # key) so search and the model are configured independently. A SecretStr so
    # it never leaks into logs or reprs. Empty by default; set via .env / env
    # vars for live use. See .env.example.
    brave_api_key: SecretStr = SecretStr("")

    # Gemini-native adapter (ADR 0020): kept separate from the shared
    # base_url/api_key above so both providers can coexist in one .env. The
    # base_url defaults to the public endpoint; the model id is configurable
    # (verify the current flash id at the provider's model list). `gemini_api_key`
    # is a SecretStr so it never leaks into logs or reprs.
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"

    # Provider-registry keys (ADR 0028): per-backend API keys for the named
    # OpenAI-compatible presets in app.services.llm.providers. Each preset owns
    # its base_url; the operator supplies only the key here (and the per-role
    # model ids above) so switching backend is a name change, not a URL edit.
    # All SecretStr so they never leak into logs or reprs; empty by default —
    # set the one(s) you use via .env / env vars. Local Ollama needs no key.
    groq_api_key: SecretStr = SecretStr("")
    nvidia_api_key: SecretStr = SecretStr("")
    huggingface_api_key: SecretStr = SecretStr("")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create a cached settings instance."""
    return Settings()


settings = get_settings()
