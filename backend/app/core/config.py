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

    # Which `SearchProvider` adapter the composition root wires (ADR 0032).
    # `"tavily"` reads `search_api_key`; `"brave"` reads `brave_api_key` — so the
    # operator selects a backend by name and supplies only that backend's key,
    # mirroring the LLM provider-registry stance (CLAUDE.md §6). An unknown name
    # or a missing key surfaces as a clear `CompositionError` at wiring time.
    search_provider: str = "tavily"

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

    # Media production fabric (ADR 0032 / 0050): the end-to-end video pipeline's
    # deterministic media seams. The stock visual adapter (`StockVisualProvider`,
    # ADR 0024) needs its own key. All SecretStr so keys never leak into
    # logs/reprs; empty by default — set the ones you use for a live render.
    # Composition shells out to the `ffmpeg` binary (ADR 0023), which is required
    # for a live render but not for the hermetic fake-backed path.
    tts_voice: str = "af_heart"
    stock_api_key: SecretStr = SecretStr("")
    # Where rendered audio + video artifacts are written by the live media seams.
    media_output_dir: str = "renders"

    # TTS fabric (ADR 0050): the supervised TTS router. `tts_backend` selects
    # which backend the *doctor* checks for readiness; at render time the
    # supervisor (CLAUDE.md §4) chooses per beat among *all* wired backends, with
    # the local, zero-cost `kokoro` as the guaranteed default + fallback. Kokoro
    # needs no service key (just the model files below), so a Kokoro-only setup
    # renders with no NVIDIA/HF/OpenAI account; nvidia/hf join the router only
    # when their key is set.
    tts_backend: str = "kokoro"

    # Local Kokoro ONNX backend (ADR 0050, the default). `kokoro_model_path` /
    # `kokoro_voices_path` point at the two files `kokoro-onnx` needs; defaults
    # are the canonical filenames (resolved relative to the working directory) so
    # the provider *constructs* with no config — the files only need to exist at
    # synth time (the doctor checks their presence). `pip install kokoro-onnx`
    # and download both files; see .env.example. No key — it runs on-device.
    kokoro_model_path: str = "kokoro-v1.0.onnx"
    kokoro_voices_path: str = "voices-v1.0.bin"

    # NVIDIA TTS NIM backend (ADR 0047, optional fallback). Wired into the router
    # only when `nvidia_tts_api_key` is set; reuses the operator's NVIDIA build
    # key but on the *speech* endpoint (a distinct base_url from the LLM NIM).
    # SecretStr so the key never leaks into logs/reprs.
    nvidia_tts_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_tts_api_key: SecretStr = SecretStr("")
    nvidia_tts_model: str = "magpie-tts-multilingual"

    # HuggingFace TTS Inference API backend (ADR 0048, optional fallback). Wired
    # into the router only when `huggingface_tts_api_key` is set; the *model* is
    # the voice (no separate voice selector). SecretStr so it never leaks.
    huggingface_tts_model: str = "hexgrad/Kokoro-82M"
    huggingface_tts_api_key: SecretStr = SecretStr("")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create a cached settings instance."""
    return Settings()


settings = get_settings()
