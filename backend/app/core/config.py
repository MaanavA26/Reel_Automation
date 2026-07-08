"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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

    # Per-role provider overrides (multi-provider fabric, #113). Empty => fall
    # back to `default_provider`. This is what tiers the fabric: route the
    # judgment-heavy roles to a capable cloud model (e.g. nvidia +
    # meta/llama-3.3-70b-instruct) while bulk roles run on a small/local one
    # (ollama + a 3B) — big models for judgment, small for bulk (CLAUDE.md §6).
    # Each names a provider the composition root can build (openai-compatible /
    # gemini / a PROVIDER_REGISTRY preset). Pooling distinct providers across
    # roles also spreads load over independent free-tier rate buckets.
    planning_provider: str = ""
    extraction_provider: str = ""
    long_context_provider: str = ""
    fallback_provider: str = ""

    # Providers (comma-separated names) that should use schema-constrained
    # decoding: the OpenAI-compatible adapter sends the caller's JSON Schema as a
    # `json_schema` response_format so the backend *constrains* output to valid
    # matching JSON. Small local models need this to satisfy strict Pydantic
    # schemas; capable cloud models ground fine without it (and may not support
    # it). Default targets the local `ollama` preset.
    llm_schema_format_providers: str = "ollama"

    # LLM resilience wiring (the ADR 0027 retry capability, wired into the
    # composition root). When `llm_retry_max_attempts` > 1 the composed
    # `ModelProvider` is wrapped in a `ResilientModelProvider` that retries
    # *transient* HTTP failures (429 rate limits, 5xx, transport faults — never
    # auth/config errors) with bounded exponential backoff. `1` (the default)
    # disables retry — the pre-wiring behavior the hermetic tests assume. The
    # delay defaults are sized for free-tier per-minute rate windows.
    llm_retry_max_attempts: int = Field(default=1, ge=1)
    llm_retry_base_delay: float = Field(default=5.0, ge=0.0)
    llm_retry_backoff_factor: float = Field(default=2.0, ge=1.0)
    llm_retry_max_delay: float = Field(default=60.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_retry_delays(self) -> Settings:
        """Reject an inverted delay ladder at parse time, not at composition."""
        if self.llm_retry_max_delay < self.llm_retry_base_delay:
            raise ValueError(
                "llm_retry_max_delay must be >= llm_retry_base_delay "
                f"(got max_delay={self.llm_retry_max_delay}, "
                f"base_delay={self.llm_retry_base_delay})"
            )
        return self

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

    # Real word-level forced alignment (ADR 0062, wired ADR 0063). `aeneas` is an
    # external subprocess contract (module docstring of `media.alignment.aeneas`),
    # never a pip dependency of this repo, so it is provisioned the same way as
    # Kokoro's model files: a bare path setting, `None` by default. When set, the
    # composition root points `AeneasAligner` at this interpreter (an environment
    # with aeneas + eSpeak installed — any venv is fine) and wires it into
    # `MediaPipeline` for word-level karaoke captions. `None` (the default) leaves
    # `word_aligner=None` — identical to the pre-ADR-0063 behavior (cue-level
    # captions only). No further validation beyond what Pydantic gives for free —
    # it is just a path string; a bad path surfaces as an `AlignmentError` at
    # align time, not at settings load time.
    aeneas_python_bin: str | None = None

    # Per-beat narration synthesis + per-clip alignment (ADR 0067, issue #159 —
    # the root-cause fix direction for the #146/#154 caption-timing bugs). When
    # true, the composition root wires a `NarrationSynthesizer` into
    # `MediaPipeline`: each script beat is synthesized as its own clip and
    # spliced with a uniform gap, so cue boundaries are exact at synthesis time,
    # and (when `aeneas_python_bin` is also set) word alignment runs per clip —
    # short tasks, immune to aeneas's cumulative long-audio DTW drift (#154).
    # False (the default) keeps the whole-narration path byte-identical to
    # pre-ADR-0067 behavior, mirroring `aeneas_python_bin`'s additive opt-in
    # pattern so the live rollout is explicit and reversible.
    narration_per_beat: bool = False

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

    # Generative-video fabric (ADR 0053): AI text-to-video adapters behind the
    # `GenerativeVisualProvider` seam, selected by `generative_video_backend`
    # (one of veo/runway/luma/pika/kling). Empty (the default) leaves the feature
    # off — the existing stock-retrieval/ffmpeg path is unaffected. Each backend
    # joins only when its credentials below are set; all keys are SecretStr so
    # they never leak into logs/reprs. Wired by `build_generative_visual_provider`
    # (capability-before-wiring: not yet plumbed into the render path; ADR 0053).
    generative_video_backend: str = ""
    # Runway / Luma — static bearer keys.
    runway_api_key: SecretStr = SecretStr("")
    luma_api_key: SecretStr = SecretStr("")
    # Pika — served via fal.ai (no first-party API); needs a fal key.
    pika_fal_api_key: SecretStr = SecretStr("")
    # Kling — JWT minted from an access key + secret key (not a static token).
    kling_access_key: SecretStr = SecretStr("")
    kling_secret_key: SecretStr = SecretStr("")
    # Veo (Vertex AI) — a GCP OAuth access token (caller mints/refreshes it; the
    # token-refresh deferral mirrors YouTube publish, ADR 0033), the GCP project
    # id + region, and a gs:// storage prefix so Veo returns a fetchable GCS uri
    # rather than inline base64.
    veo_access_token: SecretStr = SecretStr("")
    veo_project: str = ""
    veo_location: str = "us-central1"
    veo_storage_uri: str = ""

    # Closed-loop automation runner (ADR 0054): the unattended driver loop that
    # turns sourced topics into videos on a cadence, gates them, and (when
    # permitted) publishes. All defaults are the *safe* ones — nothing auto-posts
    # until the operator opts into autonomous mode AND wires a live publisher.
    #
    # `loop_mode` selects the ALLOW-branch behavior: "supervised" (default) holds
    # *every* produced video for a human approve via the reviews API and posts
    # nothing automatically; "autonomous" auto-publishes a safety-ALLOW video
    # within budget and only holds REVIEW items. AUTONOMOUS AUTO-POSTS TO REAL
    # PLATFORMS — it is the last-mile, live-key-gated income loop, OFF by default.
    loop_mode: str = "supervised"
    # Comma-separated niches the loop's topic source mines each tick (the trend
    # provider's seeds). Empty by default; a live loop must set at least one.
    loop_niches: str = ""
    # Cadence: fire every `loop_interval_seconds` (anchored interval schedule).
    # Defaults to 6h (4 fires/day) — a sane N/day baseline; tune per channel.
    loop_interval_seconds: float = 21_600.0
    # How many topics to drain + produce per tick, and the produce concurrency cap.
    loop_batch_size: int = 3
    loop_max_concurrency: int = 2
    # The loop's own per-video cost estimate + ceilings (a coarse count/cost
    # guardrail so an unattended run cannot produce/post unboundedly; distinct
    # from the model-fabric per-call budget). `None` ceilings mean "no cap".
    loop_video_cost_estimate: float = 1.0
    loop_budget_per_run: float | None = None
    loop_budget_per_day: float | None = None
    # Platform privacy for published videos. Defaults to "private" so even
    # autonomous mode never posts publicly without an explicit opt-in.
    loop_privacy_status: str = "private"

    # Topic / trend sourcing (ADR 0037): the live `HttpTrendProvider` key. Empty
    # by default; a live loop reads this to mine `loop_niches`. SecretStr so it
    # never leaks into logs/reprs.
    trends_api_key: SecretStr = SecretStr("")

    # Publishing fabric (ADR 0033): the live YouTube Shorts publisher. The OAuth
    # access token is operator-minted/refreshed (the token-refresh deferral of
    # ADR 0033); empty by default so a live publish is opt-in. SecretStr so it
    # never leaks. `publish_platform` selects which adapter the loop wires
    # ("youtube"); other platforms are protocol-conformant skeletons (ADR 0033).
    publish_platform: str = "youtube"
    youtube_access_token: SecretStr = SecretStr("")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create a cached settings instance."""
    return Settings()


settings = get_settings()
