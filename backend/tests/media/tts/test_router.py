"""Tests for the deterministic TTS fabric router (fallback + selection).

Fully hermetic: built from `FakeTTSProvider`s, one or more made to fail by a
thin failing decorator, so the ordered-fallback contract is asserted without
network or audio. Mirrors the LLM resilience tests' style.
"""

from __future__ import annotations

import asyncio

import pytest

from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import FakeTTSProvider
from app.media.tts.router import (
    TTSExhaustedError,
    TTSRouter,
    TTSRoutingError,
    UnknownBackendError,
)


class FailingTTSProvider:
    """A `TTSProvider` that always raises — to drive fallback in tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        self.calls += 1
        raise RuntimeError(f"{self.name} synthesis failed")


def _router(**providers: object) -> TTSRouter:
    # Insertion order of kwargs is the fallback order (cheapest-first by convention).
    return TTSRouter(providers=providers, fallback_order=list(providers))  # type: ignore[arg-type]


def test_synthesize_uses_default_backend_when_unspecified() -> None:
    kokoro = FakeTTSProvider()
    router = TTSRouter(
        providers={"kokoro": kokoro, "nvidia": FakeTTSProvider()},
        fallback_order=["kokoro", "nvidia"],
    )
    speech = asyncio.run(router.synthesize(text="hi", voice="v"))
    assert isinstance(speech, SynthesizedSpeech)
    assert len(kokoro.calls) == 1  # default (first in order) was used


def test_falls_back_past_multiple_failures_to_a_working_backend() -> None:
    kokoro = FailingTTSProvider("kokoro")
    nvidia = FailingTTSProvider("nvidia")
    hugging = FakeTTSProvider()
    router = TTSRouter(
        providers={"kokoro": kokoro, "nvidia": nvidia, "huggingface": hugging},
        fallback_order=["kokoro", "nvidia", "huggingface"],
    )
    speech = asyncio.run(router.synthesize(text="t", voice="v"))
    # Reached the third backend after two failures.
    assert kokoro.calls == 1
    assert nvidia.calls == 1
    assert speech.produced_via == "tts:fake"


def test_chosen_backend_is_not_tried_twice_during_traversal() -> None:
    # Choose a mid-order backend that fails; the chain should try it once, then
    # walk the rest (including the cheaper earlier one it skipped), never twice.
    kokoro = FakeTTSProvider()
    nvidia = FailingTTSProvider("nvidia")
    router = TTSRouter(
        providers={"kokoro": kokoro, "nvidia": nvidia},
        fallback_order=["kokoro", "nvidia"],
    )
    speech = asyncio.run(router.synthesize(text="t", voice="v", backend="nvidia"))
    assert nvidia.calls == 1  # tried once, not re-tried at its policy position
    assert len(kokoro.calls) == 1  # the skipped cheaper backend caught it
    assert speech.produced_via == "tts:fake"


def test_raises_only_when_all_backends_fail() -> None:
    router = _router(kokoro=FailingTTSProvider("kokoro"), nvidia=FailingTTSProvider("nvidia"))
    with pytest.raises(TTSExhaustedError) as exc:
        asyncio.run(router.synthesize(text="t", voice="v"))
    assert isinstance(exc.value.__cause__, RuntimeError)  # last provider error chained


def test_unknown_chosen_backend_raises() -> None:
    router = TTSRouter(providers={"kokoro": FakeTTSProvider()}, fallback_order=["kokoro"])
    with pytest.raises(UnknownBackendError):
        asyncio.run(router.synthesize(text="t", voice="v", backend="nope"))


def test_empty_fallback_order_rejected() -> None:
    with pytest.raises(TTSRoutingError):
        TTSRouter(providers={"kokoro": FakeTTSProvider()}, fallback_order=[])


def test_fallback_order_naming_unregistered_backend_rejected() -> None:
    with pytest.raises(TTSRoutingError):
        TTSRouter(providers={"kokoro": FakeTTSProvider()}, fallback_order=["kokoro", "ghost"])


def test_default_backend_is_first_in_policy() -> None:
    router = TTSRouter(
        providers={"kokoro": FakeTTSProvider(), "nvidia": FakeTTSProvider()},
        fallback_order=["kokoro", "nvidia"],
    )
    assert router.default_backend == "kokoro"
    assert router.available() == {"kokoro", "nvidia"}
