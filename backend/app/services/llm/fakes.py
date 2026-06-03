"""In-memory `ModelProvider` for hermetic tests (no network).

A factory-style fake (testing-standards: "don't mock what you can fake") that
replays scripted, pre-validated structured responses in order and records the
calls it received so tests can assert on routing and prompt wiring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from app.services.llm.base import StructuredT


@dataclass
class RecordedCall:
    """A single ``complete_structured`` invocation captured by the fake."""

    model: str
    system: str
    prompt: str
    schema: type[BaseModel]


class FakeProvider:
    """A `ModelProvider` that replays scripted responses in order.

    Construct with the responses a test expects, in call order. Each response
    must be an instance of the ``schema`` the caller requests, mirroring the
    real contract that a provider returns schema-validated output.
    """

    name = "fake"

    def __init__(self, responses: Sequence[BaseModel]) -> None:
        self._responses: list[BaseModel] = list(responses)
        self.calls: list[RecordedCall] = []

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[StructuredT],
    ) -> StructuredT:
        self.calls.append(RecordedCall(model=model, system=system, prompt=prompt, schema=schema))
        if not self._responses:
            raise AssertionError("FakeProvider exhausted: no scripted response left")
        response = self._responses.pop(0)
        if not isinstance(response, schema):
            raise AssertionError(
                f"scripted response {type(response).__name__} is not a {schema.__name__}"
            )
        return response
