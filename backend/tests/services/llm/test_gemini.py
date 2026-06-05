"""Tests for the Gemini-native provider adapter (M-LP.3).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building, native-structured-output wiring, the schema sanitizer, and the
error-fed repair retry are all verified without a live call. The live call itself
is covered by a separate ``@pytest.mark.integration`` smoke test (skipped without
a key).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.services.llm.base import ModelProvider
from app.services.llm.gemini import (
    DEFAULT_BASE_URL,
    GeminiError,
    GeminiProvider,
    _to_gemini_schema,
)


class _Sub(BaseModel):
    label: str


class _Out(BaseModel):
    value: str
    n: int
    note: str | None = None
    items: list[_Sub] = []


def _provider(handler: Any) -> GeminiProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiProvider(api_key="k", base_url="https://x", client=client)


def _resp(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _complete(provider: GeminiProvider) -> _Out:
    return asyncio.run(
        provider.complete_structured(model="m", system="sys", prompt="p", schema=_Out)
    )


def test_builds_request_with_native_schema_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key_header"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return _resp('{"value": "ok", "n": 3}')

    out = _complete(_provider(handler))
    assert out == _Out(value="ok", n=3)
    # generateContent endpoint with the model id in the path; key is NOT in the URL.
    assert seen["url"] == "https://x/v1beta/models/m:generateContent"
    assert "key=" not in seen["url"]
    assert seen["api_key_header"] == "k"
    # Native structured output is configured.
    gen_cfg = seen["body"]["generationConfig"]
    assert gen_cfg["responseMimeType"] == "application/json"
    assert gen_cfg["responseSchema"]["type"] == "object"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "p"


def test_response_schema_is_sanitized_for_nested_model() -> None:
    # The crux: the wire schema must inline $ref/$defs and drop Gemini-rejected
    # keys, or the live call 400s before the repair loop can ever run.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _resp('{"value": "ok", "n": 1, "items": [{"label": "a"}]}')

    out = _complete(_provider(handler))
    assert out.items == [_Sub(label="a")]

    wire = json.dumps(seen["body"]["generationConfig"]["responseSchema"])
    assert "$ref" not in wire
    assert "$defs" not in wire
    assert "additionalProperties" not in wire
    assert "title" not in wire
    # The nested model was inlined as the array item type.
    schema = seen["body"]["generationConfig"]["responseSchema"]
    assert schema["properties"]["items"]["items"]["properties"]["label"]["type"] == "string"


def test_optional_field_becomes_nullable() -> None:
    # Pydantic encodes Optional[str] as anyOf[str, null]; the sanitizer collapses
    # it to a single nullable branch (no anyOf-with-null on the wire).
    wire = _to_gemini_schema(_Out.model_json_schema())
    note = wire["properties"]["note"]
    assert "anyOf" not in note
    assert note.get("nullable") is True
    assert note["type"] == "string"


def test_strips_prose_and_fences() -> None:
    out = _complete(_provider(lambda r: _resp('```json\n{"value": "x", "n": 1}\n```')))
    assert out == _Out(value="x", n=1)


def test_error_fed_retry_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp('{"value": "missing n"}')  # invalid: required `n` absent
        return _resp('{"value": "ok", "n": 2}')

    out = _complete(_provider(handler))
    assert out.n == 2
    assert calls["n"] == 2  # one repair retry happened

    # The repair turn fed the bad output + error back as conversation context.
    # (We can't see it here, but the second call succeeding proves the loop ran.)


def test_raises_after_repair_exhausted() -> None:
    with pytest.raises(GeminiError):
        _complete(_provider(lambda r: _resp('{"value": "no n ever"}')))


def test_http_error_surfaces() -> None:
    # A 400 (e.g. bad schema) propagates as httpx, NOT swallowed by the repair loop.
    with pytest.raises(httpx.HTTPStatusError):
        _complete(_provider(lambda r: httpx.Response(400, json={"error": "bad request"})))


def test_safety_blocked_response_raises() -> None:
    # No candidates / no parts (safety block) surfaces as a typed GeminiError.
    with pytest.raises(GeminiError):
        _complete(_provider(lambda r: httpx.Response(200, json={"candidates": []})))


def test_missing_api_key_raises() -> None:
    with pytest.raises(GeminiError):
        GeminiProvider(api_key="")


def test_default_base_url_is_public_endpoint() -> None:
    provider = GeminiProvider(api_key="k")
    assert DEFAULT_BASE_URL == "https://generativelanguage.googleapis.com"
    assert provider.name == "gemini"


def test_satisfies_model_provider_protocol() -> None:
    # The factory is intentionally not wired for Gemini (scope), so no other site
    # checks protocol conformance. This guards it explicitly.
    assert isinstance(GeminiProvider(api_key="k"), ModelProvider)
