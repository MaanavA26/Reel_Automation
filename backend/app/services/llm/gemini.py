"""Gemini-native ``ModelProvider`` adapter (httpx over the REST API).

Speaks the Gemini ``generateContent`` REST endpoint directly via ``httpx`` (a
runtime dependency), so request building and the response → schema mapping are
unit-testable **offline** via ``httpx.MockTransport``; only the live call needs
network. See ADR 0020.

Why this exists alongside the OpenAI-compatible adapter (ADR 0007): the value
here is **native structured output**. Gemini accepts a ``responseSchema`` plus
``responseMimeType: "application/json"`` and constrains decoding to that schema
server-side, so the model returns schema-shaped JSON directly rather than the
schema-in-prompt-then-hope strategy. We still ``model_validate_json`` the result
(the server schema is an OpenAPI subset, not our full Pydantic contract) and keep
**one error-fed repair retry** to mirror ADR 0007's reliability boundary.

Auth uses the ``x-goog-api-key`` header (never the ``?key=`` query parameter) so
the key cannot leak into request-URL logs.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from app.services.llm.base import StructuredT

PROVIDER_NAME = "gemini"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

# Keys emitted by Pydantic's ``model_json_schema()`` that Gemini's
# ``responseSchema`` (an OpenAPI 3.0 subset) rejects with a 400. Stripped by the
# sanitizer below. ``$defs``/``$ref`` are handled separately (inlined).
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"title", "additionalProperties", "default", "$schema", "$defs", "definitions"}
)


class GeminiError(RuntimeError):
    """Raised on transport failure or output that fails validation after repair."""


class GeminiProvider:
    """A ``ModelProvider`` over the Gemini ``generateContent`` REST API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        max_repair_retries: int = 1,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise GeminiError("api_key is required (set REEL_AUTOMATION_GEMINI_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
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
        response_schema = _to_gemini_schema(schema.model_json_schema())
        # Conversation turns; on a repair we append the model's bad output and the
        # validation error as further turns, mirroring the OpenAI-compatible adapter.
        contents: list[dict[str, Any]] = [
            {"role": "user", "parts": [{"text": prompt}]},
        ]

        text = await self._generate(model, system, contents, response_schema)
        last_error: Exception | None = None
        for attempt in range(self._max_repair_retries + 1):
            try:
                return schema.model_validate_json(_extract_json(text))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if attempt == self._max_repair_retries:
                    break
                # Error-fed repair: show the model its bad output + the error.
                contents.append({"role": "model", "parts": [{"text": text}]})
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Your previous response failed validation: {exc}. "
                                    "Return ONLY a corrected JSON object matching the schema."
                                )
                            }
                        ],
                    }
                )
                text = await self._generate(model, system, contents, response_schema)

        raise GeminiError(
            f"model output failed schema validation after "
            f"{self._max_repair_retries} repair attempt(s): {last_error}"
        )

    async def _generate(
        self,
        model: str,
        system: str,
        contents: list[dict[str, Any]],
        response_schema: dict[str, Any],
    ) -> str:
        response = await self._client.post(
            f"{self._base_url}/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": response_schema,
                    "temperature": 0,
                },
            },
        )
        response.raise_for_status()
        data: Any = response.json()
        try:
            return str(data["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            # Also covers a safety-blocked / empty-candidate response (no parts).
            raise GeminiError(f"unexpected generateContent response shape: {data!r}") from exc


def _to_gemini_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a Pydantic JSON Schema into Gemini's ``responseSchema`` subset.

    Gemini's ``responseSchema`` is an OpenAPI 3.0 subset, not full JSON Schema. A
    raw ``model_json_schema()`` carries ``$defs``/``$ref`` (for nested models) and
    keys like ``title``/``additionalProperties``/``default`` that Gemini rejects
    with a 400. This recursively **inlines** ``$ref`` targets and strips the
    unsupported keys. ``anyOf`` with a ``{"type": "null"}`` branch (Pydantic's
    encoding of ``Optional[X]``) is collapsed to the non-null branch marked
    ``nullable``; other ``anyOf`` is passed through (Gemini supports it).

    This is intentionally bounded — not a general JSON-Schema compiler. Exotic
    constructs (e.g. ``patternProperties``) are not handled; see ADR 0020.
    """
    # ``$defs`` is only present at the document root; thread it down so nested
    # ``$ref``s resolve against it.
    resolved_defs = defs if defs is not None else schema.get("$defs", {})

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], resolved_defs)
        return _to_gemini_schema(target, resolved_defs)

    if "anyOf" in schema:
        branches = [b for b in schema["anyOf"] if b.get("type") != "null"]
        nullable = len(branches) != len(schema["anyOf"])
        if len(branches) == 1:
            converted = _to_gemini_schema(branches[0], resolved_defs)
            if nullable:
                converted["nullable"] = True
            return converted
        return {"anyOf": [_to_gemini_schema(b, resolved_defs) for b in branches]}

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                name: _to_gemini_schema(prop, resolved_defs) for name, prop in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            out["items"] = _to_gemini_schema(value, resolved_defs)
        else:
            out[key] = value
    return out


def _resolve_ref(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local ``#/$defs/Name`` reference against the collected defs."""
    name = ref.rsplit("/", 1)[-1]
    try:
        target = defs[name]
    except KeyError as exc:
        raise GeminiError(f"cannot resolve schema $ref {ref!r}") from exc
    return dict(target)


# Defensive: with ``responseMimeType: application/json`` Gemini returns clean
# JSON, but a stray fence/prose costs nothing to tolerate before validation.
def _extract_json(content: str) -> str:
    """Slice to the outer JSON object, tolerating prose or markdown fences."""
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        return content[start : end + 1]
    return content.strip()
