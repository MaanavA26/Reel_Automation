# Configuration Reference

The authoritative reference for every environment variable and provider option
in **Reel Automation**. For how to run and deploy the system, see
[`operations.md`](operations.md).

All settings are defined in `backend/app/core/config.py` (the `Settings` class,
a `pydantic-settings` `BaseSettings`). Every field is read from an environment
variable with the prefix **`REEL_AUTOMATION_`** (field name uppercased), or from
a `.env` file in the process working directory. Unknown env vars are ignored
(`extra="ignore"`). A template lives at `backend/.env.example`.

> **Accuracy note.** This reference documents what the code reads and what is
> actually wired. Several adapters exist as code but are **not yet connected**
> to the running service; those are called out explicitly. Do not assume a field
> activates a feature unless this page says so.

---

## Contents

- [Environment variable reference](#environment-variable-reference)
- [Frontend build variable](#frontend-build-variable)
- [LLM providers](#llm-providers)
- [Search providers](#search-providers)
- [Ingestion providers](#ingestion-providers)
- [Wired-vs-present summary](#wired-vs-present-summary)

---

## Environment variable reference

Every field on `Settings`, its env var, default, type, and what it configures.
The env var is the field name uppercased with the `REEL_AUTOMATION_` prefix.

| Env var | Default | Type | What it configures |
| --- | --- | --- | --- |
| `REEL_AUTOMATION_APP_NAME` | `reel-automation` | str | Application name. Cosmetic; no behavioral effect on the request path. |
| `REEL_AUTOMATION_API_V1_PREFIX` | `/api/v1` | str | URL prefix the v1 API router mounts under (e.g. health → `<prefix>/health`). |
| `REEL_AUTOMATION_DEFAULT_PROVIDER` | `anthropic` | str | Name of the LLM provider the router uses for every model role. **Only `openai-compatible` is wired** in the factory today; the `anthropic` default therefore fails until overridden (see [LLM providers](#llm-providers)). |
| `REEL_AUTOMATION_PLANNING_MODEL` | `claude-opus-4-8` | str | Model id for the `PLANNING` role (planning / reasoning). |
| `REEL_AUTOMATION_EXTRACTION_MODEL` | `claude-sonnet-4-6` | str | Model id for the `EXTRACTION` role (structured extraction). |
| `REEL_AUTOMATION_LONG_CONTEXT_MODEL` | `claude-opus-4-8` | str | Model id for the `LONG_CONTEXT` role (long-context summarization / synthesis). |
| `REEL_AUTOMATION_FALLBACK_MODEL` | `claude-haiku-4-5-20251001` | str | Model id for the `FALLBACK` role (budget / fallback). |
| `REEL_AUTOMATION_BASE_URL` | `""` (empty) | str | Base URL for the **OpenAI-compatible** LLM adapter (e.g. `https://api.groq.com/openai/v1`). |
| `REEL_AUTOMATION_API_KEY` | `""` (empty) | SecretStr | API key for the OpenAI-compatible LLM adapter. `SecretStr` — never appears in logs or reprs. |
| `REEL_AUTOMATION_SEARCH_API_KEY` | `""` (empty) | SecretStr | Key intended for the Tavily search adapter. **Read by `Settings` but not consumed by any wired code today** (search is not wired — see [Search providers](#search-providers)). |
| `REEL_AUTOMATION_BRAVE_API_KEY` | `""` (empty) | SecretStr | Key intended for the Brave search adapter. **Read by `Settings` but not consumed by any wired code today.** |
| `REEL_AUTOMATION_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com` | str | Base URL for the Gemini-native adapter. **Read by `Settings` but not consumed by any wired code today** (Gemini adapter is not wired into the router factory). |
| `REEL_AUTOMATION_GEMINI_API_KEY` | `""` (empty) | SecretStr | Key for the Gemini-native adapter. **Read by `Settings` but not consumed by any wired code today.** |
| `REEL_AUTOMATION_GEMINI_MODEL` | `gemini-2.5-flash` | str | Model id for the Gemini-native adapter. **Read by `Settings` but not consumed by any wired code today.** |

All `SecretStr` fields are masked in logs and object reprs; their value is only
read explicitly by the adapter that needs it.

---

## Frontend build variable

`VITE_API_BASE_URL` is **not** part of the backend `Settings`. It is a
Vite/React **build-time** variable inlined into the static bundle (it tells the
browser where the API lives). It is set as a Docker build arg in
`docker-compose.yml` (default `http://localhost:8000`) and consumed by
`frontend/Dockerfile`. Changing it requires a frontend rebuild — it cannot be
changed at runtime.

---

## LLM providers

The LLM fabric (CLAUDE.md §6) routes each model **role** (planning, extraction,
long-context, fallback) to a `ModelChoice(provider, model)` via a config-sourced
policy. The provider is selected by `REEL_AUTOMATION_DEFAULT_PROVIDER`; the model
ids are the four `*_MODEL` fields above.

| Provider | Selector value | Connection fields | Status |
| --- | --- | --- | --- |
| OpenAI-compatible | `openai-compatible` | `BASE_URL`, `API_KEY`, the four `*_MODEL` ids | **Wired & selectable.** The only provider the router factory builds. |
| Gemini (native) | `gemini` | `GEMINI_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL` | **Adapter present, not wired.** `GeminiProvider` exists but is not registered in `factory.py`; selecting it raises. |
| Anthropic | `anthropic` (the default) | — | **No adapter.** This is the default value but has no registered adapter, so the default config fails until overridden. |

### Selecting the OpenAI-compatible provider

This is the supported path today. One adapter speaks any OpenAI-compatible
backend — switch providers by changing `BASE_URL` + `API_KEY` + model ids only,
no code change. The shipped `backend/.env.example` is preconfigured for Groq:

```bash
REEL_AUTOMATION_DEFAULT_PROVIDER=openai-compatible
REEL_AUTOMATION_BASE_URL=https://api.groq.com/openai/v1
REEL_AUTOMATION_API_KEY=gsk_your_key_here
REEL_AUTOMATION_PLANNING_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_EXTRACTION_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_LONG_CONTEXT_MODEL=llama-3.3-70b-versatile
REEL_AUTOMATION_FALLBACK_MODEL=llama-3.1-8b-instant
```

Other OpenAI-compatible backends use the same fields with a different
`BASE_URL` + key + model ids:

| Backend | `BASE_URL` | Key format |
| --- | --- | --- |
| Groq | `https://api.groq.com/openai/v1` | `gsk_...` |
| OpenRouter | `https://openrouter.ai/api/v1` | `sk-or-...` (many `:free` model ids) |
| Together AI | `https://api.together.xyz/v1` | provider key |
| Local Ollama | `http://localhost:11434/v1` | any non-empty string |

Verify current model ids at the provider's model list before use — they change.
For deeper model-selection guidance, see
[`docs/llm-model-selection.md`](llm-model-selection.md).

---

## Search providers

Two live search adapters exist in `backend/app/services/search/` — **Tavily**
(`live.py`, `POST /search` + `Authorization: Bearer`) and **Brave**
(`brave_search.py`, `GET /res/v1/web/search` + `X-Subscription-Token`). Both
implement the provider-neutral `SearchProvider` protocol and return web results.

| Provider | Intended key field | Status |
| --- | --- | --- |
| Tavily | `SEARCH_API_KEY` | Adapter present, **not wired** into the composition root. |
| Brave | `BRAVE_API_KEY` | Adapter present, **not wired** into the composition root. |

**There is no environment-only way to activate search today.** The composition
root's `_build_search_provider` (`backend/app/services/composition.py`)
unconditionally raises `CompositionError` — the live adapter is network-gated and
deferred (M-LP). The `SEARCH_API_KEY` and `BRAVE_API_KEY` fields are read into
`Settings` but are not consumed by any wired code path. Setting them has no
effect on the running service yet. (See `operations.md` →
[Known limitations](operations.md#known-limitations).)

---

## Ingestion providers

The ingestion service (`backend/app/services/ingestion/service.py`) is a
deterministic tool that turns discovered `Source`s into `Chunk`s, dispatching by
source type. The composition root builds it as
`IngestionService(HttpxFetchProvider())` — i.e. with the web fetcher and the
default `pypdf` parser, and **no** transcript provider.

| Source type | Provider / parser | Status |
| --- | --- | --- |
| WEB | `HttpxFetchProvider` + stdlib HTML parser | **Wired.** Hardened fetch (timeout, size/redirect caps, content-type allowlist, no credentials). |
| PDF | `HttpxFetchProvider` + `PypdfParser` (text layer) | **Wired.** Text layer only; scanned/image PDF (OCR) not supported. A missing `pypdf` surfaces as a per-source skip. |
| YOUTUBE | `YouTubeTranscriptProvider` | **Adapter present, not wired.** The service is built without a `transcript_provider`, so YouTube sources are skipped. Also requires the optional `youtube` extra (`pip install -e "./backend[youtube]"`). |
| Other types | — | Skipped (logged). |

Per-source fetch/parse failures are tolerated (skipped + logged); ingestion
raises only when **no** chunk is produced from any source. There are no
environment variables for ingestion configuration today — provider selection is
code-level (the composition root), not env-driven.

---

## Wired-vs-present summary

A one-glance map of what an operator can actually configure today versus what is
scaffolded but inert.

| Capability | Configurable via env today? | Notes |
| --- | --- | --- |
| OpenAI-compatible LLM | **Yes** | `DEFAULT_PROVIDER=openai-compatible` + `BASE_URL` + `API_KEY` + `*_MODEL`. |
| Per-role model ids | **Yes** | The four `*_MODEL` fields. |
| API prefix | **Yes** | `API_V1_PREFIX`. |
| Gemini LLM | No | Adapter + `GEMINI_*` fields exist; not wired into the router factory. |
| Anthropic LLM | No | Default `DEFAULT_PROVIDER` value; no adapter registered. |
| Tavily / Brave search | No | Adapters + key fields exist; composition root raises (no wired search). |
| WEB / PDF ingestion | n/a (always on, no env knobs) | Wired by default in the composition root. |
| YouTube ingestion | No | Adapter exists; not wired and needs the optional `youtube` extra. |
| Frontend API base | Build-time only | `VITE_API_BASE_URL` build arg, not a runtime `Settings` field. |
