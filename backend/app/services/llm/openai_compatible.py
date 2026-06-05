"""OpenAI-compatible ``ModelProvider`` adapter (httpx-based).

Speaks the OpenAI ``/chat/completions`` API, so a *single* adapter serves any
compatible backend — Groq, OpenRouter, Together, Cerebras, local Ollama —
selected entirely by ``base_url`` + ``api_key`` + ``model`` (configuration, no
code change; CLAUDE.md §6 policy-driven routing).

Built on ``httpx`` (a runtime dependency) so request building and the
response → schema mapping are unit-testable **offline** via
``httpx.MockTransport``; only the live call needs network. See ADR 0007.

Structured-output strategy: JSON-object mode + the caller's JSON Schema injected
into the system prompt + ``model_validate_json``, with one *error-fed* repair
retry. Strict ``json_schema`` mode is deliberately not relied upon — it is not
portable across the free OpenAI-compatible backends this adapter targets.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.lifecycle import CloseOwnedClientMixin
from app.services.llm.base import StructuredT

PROVIDER_NAME = "openai-compatible"

# Bound the upstream-body excerpt in error messages so a full provider response
# never lands in ``ResearchState.error`` / logs (info-leak guard, ADR 0043).
_ERR_BODY_MAX = 500


class OpenAICompatError(RuntimeError):
    """Raised on transport failure or output that fails validation after repair."""


class OpenAICompatibleProvider(CloseOwnedClientMixin):
    """A ``ModelProvider`` over the OpenAI chat-completions API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        max_repair_retries: int = 1,
        timeout: float = 60.0,
    ) -> None:
        if not base_url:
            raise OpenAICompatError("base_url is required (set REEL_AUTOMATION_BASE_URL)")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._max_repair_retries = max_repair_retries

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        schema_json = json.dumps(schema.model_json_schema())
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"{system}\n\nReturn ONLY a JSON object conforming to this JSON "
                    f"Schema (no prose, no markdown fences):\n{schema_json}"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        content = await self._chat(model, messages)
        last_error: Exception | None = None
        for attempt in range(self._max_repair_retries + 1):
            try:
                return schema.model_validate_json(_extract_json(content))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if attempt == self._max_repair_retries:
                    break
                # Error-fed repair: show the model its bad output + the error.
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed validation: {exc}. "
                            "Return ONLY a corrected JSON object matching the schema."
                        ),
                    }
                )
                content = await self._chat(model, messages)

        raise OpenAICompatError(
            f"model output failed schema validation after "
            f"{self._max_repair_retries} repair attempt(s): {last_error}"
        )

    async def _chat(self, model: str, messages: list[dict[str, str]]) -> str:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
        response.raise_for_status()
        data: Any = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenAICompatError(
                f"unexpected chat-completions response shape: {repr(data)[:_ERR_BODY_MAX]}"
            ) from exc


def _extract_json(content: str) -> str:
    """Slice to the outer JSON object, tolerating prose or markdown fences."""
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        return content[start : end + 1]
    return content.strip()
