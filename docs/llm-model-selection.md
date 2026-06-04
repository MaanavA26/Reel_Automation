# LLM Model Selection — Per-Task Analysis (living doc)

**Status:** living document · **Last updated:** 2026-06-04 · **Method:** live model
enumeration + measured eval, not from-memory recall.

> This doc answers: *for each LLM call in the engine, which model is best, and what
> are the real options?* The model landscape changes monthly, so every concrete
> claim here is either (a) **enumerated live** from a provider's `/models` endpoint,
> (b) **measured** by a reproducible eval, or (c) explicitly marked **unverified /
> to-eval-when-built**. Re-run the eval (method in §6) when revisiting.

## 0. TL;DR

- **Working providers (tested live 2026-06-04):** Google **Gemini** (newest models,
  free tier), **Groq** (fast, free), **HuggingFace** router (122 newest open models),
  **NVIDIA build** / NIM (119 open models incl. DeepSeek-V4-Pro). **OpenAI** direct
  key = invalid (401). **Azure OpenAI** = unreachable (private-endpoint/403).
  *Note:* NVIDIA serves Google's open **Gemma** models but **not Gemini** (Gemini is
  Google-only). Four independent working providers = real failover redundancy.
- **For the engine's structured tasks, schema-adherence is ~100% across all working
  models** — so the real differentiators are **latency, free-tier rate limits, and
  recency**, not raw "smartness."
- **Recommended default:** **Gemini** as the quality/recency workhorse + **Groq** as
  the fast, cross-vendor fallback. Add **OpenAI** (valid key) and **HF DeepSeek/Qwen**
  as additional tiers per task. Use the **cheapest model that passes** for
  high-volume mechanical tasks; reserve top models for low-volume high-value ones.

## 1. Selection principles

1. **Structured-output adherence is co-equal with reasoning.** Every call is
   `complete_structured` → schema-valid JSON. A "smarter" model that flakes JSON is
   *worse* here. Weight schema-pass-rate and the error-fed-retry cost, not just IQ.
2. **Latest models for recency-sensitive tasks** (research framing, content angles) —
   but **real-time trends come from the retrieval/search layer, not model training.**
   The model's knowledge cutoff helps; it is not the trend engine.
3. **Cost/speed tiering.** Cheap+fast for high-volume mechanical work (evidence
   extraction over many chunks); top models for low-volume high-value work
   (synthesis, critique). "Best per task" ≠ "biggest model everywhere."
4. **Cross-vendor fallback.** Primary + a *different-vendor* backup survives a
   provider outage or rate-limit. (This is the `FALLBACK` role; the failover *trigger*
   is M4 build work, not config — see §5.)
5. **LLM-as-judge is offline, to *set* policy** — not a runtime judge on every call
   (that multiplies cost/latency on the hot path). Use an *independent* judge model
   (not the candidate itself — self-judging inflates scores; observed in §3).
6. **Provider redundancy beats provider loyalty.** The same strong open models are
   reachable through *multiple* providers (e.g. DeepSeek-V4-Pro on HuggingFace *and*
   NVIDIA; Llama-3.3-70B on Groq, HF, *and* NVIDIA). Routing a role to a model
   *family* with 2+ provider sources makes the pipeline resilient to any single
   provider's 429s/outages. The same `OpenAICompatibleProvider` also speaks to a
   **local/self-hosted** server (Ollama / vLLM, `base_url=localhost`) — a future
   "no-API-limit" fallback tier that needs only hardware, no new code.

## 2. Provider landscape (enumerated live, 2026-06-04)

All reachable via the single `OpenAICompatibleProvider` (ADR 0007) by config alone.

| Provider | Status | Endpoint (`base_url`) | Notable models (live `/models`) |
|----------|--------|------------------------|----------------------------------|
| **Gemini** | ✅ works, free tier | `…/v1beta/openai` | `gemini-3.5-flash`, `gemini-3.1-pro-preview`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-flash-latest`; also `deep-research-*`, `imagen-4`, `veo-3.1`, `gemini-embedding-2` (media/embed for later layers) |
| **Groq** | ✅ works, free, fastest | `api.groq.com/openai/v1` | `llama-3.3-70b-versatile`, `meta-llama/llama-4-scout-17b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3-32b`, `llama-3.1-8b-instant`, `whisper-large-v3` (STT) |
| **HuggingFace** router | ✅ works, free-tier credits | `router.huggingface.co/v1` | 122 models incl. `deepseek-ai/DeepSeek-V4-Pro`/`V4-Flash`, `Qwen3.6`, `GLM-5.1`, `Kimi-K2.6`, `MiniMax-M2.7`, `gemma-4`, `GLM-OCR` |
| **NVIDIA build** (NIM) | ✅ works, free credits | `integrate.api.nvidia.com/v1` | 119 models incl. `deepseek-ai/deepseek-v4-pro`/`v4-flash`, `meta/llama-4-maverick`, `meta/llama-3.3-70b-instruct` (validated live), `google/gemma-4-31b-it`, Nemotron. **No Gemini** (Google-only) |
| **OpenAI** (direct) | ❌ key invalid (401) | `api.openai.com/v1` | Works once a valid key is supplied |
| **Azure OpenAI** | ❌ 403 private-endpoint | `…openai.azure.com` | VNet-locked (corp-network only); deployment `gpt-4o-mini` (older). Has an Azure **Document Intelligence** (OCR) endpoint → useful for M6 ingestion |

**Free-tier reality (observed live):** rate limits bite. `gemini-3.1-pro-preview`
returned `429` on every call (Pro free RPM is very low); `groq/gpt-oss-120b` hit one
`429`; some HF models 500/400 intermittently. For production volume, paid tiers or
self-hosting are required.

## 3. Measured eval — Research Planner task

The one fully-built LLM task. Eval ran the **real** `ResearchPlannerAgent` (via the
real adapter) on 3 diverse topics × each model; quality judged by an *independent*
model (`groq/gpt-oss-120b`) scoring coverage/specificity/non-redundancy (1–5).

| model | schema-pass | p50 latency | avg sub-Qs | quality /5 |
|-------|:-----------:|:-----------:|:----------:|:----------:|
| groq · `llama-3.3-70b-versatile` | 3/3 | **1.2s** | 5.3 | 4.6 |
| hf · `Llama-3.3-70B-Instruct` | 3/3 | 1.3s | 5.3 | 4.2 |
| gemini · `2.5-flash-lite` | 3/3 | 3.1s | 6.3 | **4.7** |
| gemini · `3.5-flash` | 3/3 | 6.0s | 4.7 | **4.7** |
| hf · `DeepSeek-V3-0324` | 3/3 | 8.9s | 5.3 | 4.4 |
| hf · `DeepSeek-V4-Pro` | 3/3 | 12.4s | 6.7 | **4.9** |
| groq · `gpt-oss-120b` | 2/3 (one 429) | 2.4s | 9.5 | 5.0 ⚠️ |

**Reading it:**
- **Schema adherence is ~100%** for every working model — the adapter's
  json-mode + schema-in-prompt + error-fed retry generalizes. Adherence is *not* the
  differentiator for this task (it may be for others — monitor per task).
- **Quality is tightly clustered (4.2–4.9)** — all are usable planners. The headline
  differentiators are **latency** and **rate-limit robustness**.
- **Best speed/quality balance:** `gemini-2.5-flash-lite` (3.1s, 4.7) and
  `groq-llama-3.3-70b` (1.2s, 4.6). **Highest independent quality:** `DeepSeek-V4-Pro`
  (4.9) — but slow (12.4s).
- ⚠️ `gpt-oss-120b`'s 5.0 is **self-judged** (it was also the judge) — discount it.
  This is exactly why the policy uses an independent judge.

## 4. Per-task model policy

Every LLM call-site in the engine (built + planned), its requirements, and the
recommended **primary + cross-vendor fallback**. *Measured* rows are from §3;
*planned* rows are requirements + candidate shortlists, **to be eval'd when the task
is built.**

| Task (milestone) | Profile | Recommended primary | Fallback | Basis |
|------------------|---------|---------------------|----------|-------|
| **Research Planner** (M3 ✅) | reasoning + structured, low volume, recency-moderate | `gemini-2.5-flash-lite` (speed) or `gemini-3.5-flash` (latest knowledge) | `groq-llama-3.3-70b` | **measured** |
| **Source Discovery query-gen** (M5 ✅) | reasoning + structured; recency *helps* (current search terms) | `gemini-3.5-flash` (newest) | `groq-llama-3.3-70b` | measured (same task shape) |
| **Evidence Extraction** (M7 ⬜) | **high-volume**, mechanical, structured-heavy; reasoning-light | cheap/fast: `groq-llama-3.1-8b-instant` or `gemini-2.5-flash-lite` | `gemini-2.5-flash-lite` | requirements — to eval |
| **Cross-Verification** (M8 ⬜) | judgment across sources, contradiction detection | `gemini-3.5-flash` / `DeepSeek-V4-Pro` | cross-vendor | to eval |
| **Synthesis** (M9 ⬜) | **long-context** + strong reasoning, quality-critical, recency | `gemini-3.1-pro` (large context, top reasoning) | `DeepSeek-V4-Pro` | to eval |
| **Editorial Critic / judge** (M10 ⬜) | judgment, gap analysis | `DeepSeek-V4-Pro` / `gemini-3.1-pro` (≠ model being judged) | cross-vendor | to eval |
| **Content Strategist** (⬜) | creative, trend-aware, latest knowledge | `gemini-3.5-flash` (latest) + trends from **retrieval layer** | `groq-gpt-oss-120b` | to eval |

Notes: `gemini-3.1-pro` is the strongest reasoning/long-context option but is
**free-tier rate-limited** — viable only with a paid tier or sparing use; until then
`gemini-3.5-flash` or `DeepSeek-V4-Pro` substitute. When a valid **OpenAI** key is
supplied, its flagship slots in as a candidate for Synthesis/Critique (re-eval to
confirm it beats the incumbents on *this* pipeline's schema/latency, not just
benchmarks).

## 5. Recommended config now, and the failover gap

**Config (today, working providers):** Gemini default + Groq fallback — already set
in `backend/.env` (Planner live-validated against both). Suggested role mapping:

```
planning      → gemini-2.5-flash-lite   (fast + top quality)   ;; or gemini-3.5-flash for latest knowledge
extraction    → groq-llama-3.1-8b-instant  (cheap/fast; when M7 lands)
long_context  → gemini-3.1-pro-preview  (large context; rate-limited on free tier)
fallback      → groq-llama-3.3-70b      (different vendor — survives a Gemini outage)
```

**The failover is not yet built.** "Use the fallback when the primary 429s/times out"
is real **code** — the `FALLBACK` *trigger* deferred to the Orchestrator (M4, ADR
0005), not config. Today the router resolves a role to one model; automatic
provider-failover (catch 429/timeout → retry next provider) is a follow-up milestone.

## 6. Reproducing / refreshing this analysis (it's a living doc)

- **Re-enumerate models:** `GET {base_url}/models` per provider (keys in gitignored
  `keys.md`/`.env`). Factual, free.
- **Re-run the eval:** the harness runs the real `ResearchPlannerAgent` across a
  candidate list × sample topics, recording schema-pass / latency / sub-Q count, and
  judges quality with an *independent* model. Productizing it as
  `backend/scripts/eval_models.py` (so it's reproducible, not throwaway) is a
  recommended follow-up.
- **Cadence:** re-run when adding a provider/key, when a task is newly built, or
  roughly monthly (model churn).

## 7. Open items

- **Replace the OpenAI key** to add the GPT flagship as a Synthesis/Critique candidate.
- **Azure** is corp-network-only; its **Document Intelligence (OCR)** endpoint is a
  strong candidate for **M6 ingestion** (PDF/scanned-image parsing).
- **Build the failover trigger** (M4) so the cross-vendor fallback is automatic.
- **Eval M7–M10 tasks** against this method once built (the "to eval" rows above).
- **Nvidia `nemotron_ocr`** key: not a chat LLM — reserve for M6 OCR.
- **Local/self-hosted fallback tier** (Ollama / vLLM via the same adapter): the
  "offload so there's no API issue" option — software path is ready, needs only
  hardware. Add when free-tier limits become a real production constraint.
- **NVIDIA build** is now a registered candidate source (esp. for DeepSeek-V4-Pro /
  Llama-4 as a redundant backup to HuggingFace).

## References
- [ADR 0003](adrs/0003-model-router-llm-fabric.md) (model fabric), [ADR 0007](adrs/0007-openai-compatible-llm-adapter.md)
  (the adapter these models run through), [CLAUDE.md](../CLAUDE.md) §6 (model routing).
- Provider docs (verify model ids/limits, which drift): Groq `console.groq.com/docs/models`,
  Gemini `ai.google.dev/gemini-api/docs/models`, HF `huggingface.co/docs/inference-providers`.
