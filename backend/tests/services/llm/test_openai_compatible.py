"""Tests for the OpenAI-compatible provider adapter (M-LP.1).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building, response→schema mapping, and the error-fed repair retry are
all verified without a live call. The live call itself is covered by a separate
``@pytest.mark.integration`` smoke test (skipped without a key).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.services.llm.openai_compatible import OpenAICompatError, OpenAICompatibleProvider


class _Out(BaseModel):
    value: str
    n: int


def _provider(handler: Any) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(base_url="https://x/v1", api_key="k", client=client)


def _resp(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _complete(provider: OpenAICompatibleProvider) -> _Out:
    return asyncio.run(
        provider.complete_structured(model="m", system="sys", prompt="p", schema=_Out)
    )


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return _resp('{"value": "ok", "n": 3}')

    out = _complete(_provider(handler))
    assert out == _Out(value="ok", n=3)
    assert seen["url"] == "https://x/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    # The caller's JSON Schema is injected into the system message.
    assert "JSON" in seen["body"]["messages"][0]["content"]
    assert seen["body"]["messages"][1] == {"role": "user", "content": "p"}


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


def test_raises_after_repair_exhausted() -> None:
    with pytest.raises(OpenAICompatError):
        _complete(_provider(lambda r: _resp('{"value": "no n ever"}')))


def test_http_error_surfaces() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _complete(_provider(lambda r: httpx.Response(429, json={"error": "rate limited"})))


def test_missing_base_url_raises() -> None:
    with pytest.raises(OpenAICompatError):
        OpenAICompatibleProvider(base_url="", api_key="k")
