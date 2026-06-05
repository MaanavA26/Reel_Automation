"""Tests for the TTS Supervisor agent (judgment) over the TTS fabric.

Hermetic: a `FakeProvider`-backed `ModelRouter` scripts the supervisor's choice,
and a `FakeTTSProvider`-backed `TTSRouter` executes it. Asserts the agent's
contract: it picks a backend, feeds the real available set to the model, clamps
an invalid choice to the router default, and always returns synthesized audio
(the deterministic guarantee).
"""

from __future__ import annotations

import asyncio

from app.agents.tts_supervisor import (
    SYSTEM_PROMPT,
    SupervisedSpeech,
    TTSSupervisorAgent,
    _SupervisorChoice,
)
from app.media.tts.base import FakeTTSProvider
from app.media.tts.router import TTSRouter
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


def _supervisor(
    choice: _SupervisorChoice,
) -> tuple[TTSSupervisorAgent, FakeProvider, TTSRouter]:
    fake_model = FakeProvider([choice])
    model_router = ModelRouter(
        providers={"fake": fake_model},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    tts_router = TTSRouter(
        providers={"kokoro": FakeTTSProvider(), "nvidia": FakeTTSProvider()},
        fallback_order=["kokoro", "nvidia"],
    )
    return TTSSupervisorAgent(model_router, tts_router), fake_model, tts_router


def test_supervisor_picks_backend_and_returns_audio() -> None:
    agent, _, _ = _supervisor(
        _SupervisorChoice(backend="nvidia", voice="crisp-en", rationale="energetic beat")
    )
    result = asyncio.run(agent.synthesize(text="hook line"))
    assert isinstance(result, SupervisedSpeech)
    assert result.decision.backend == "nvidia"
    assert result.decision.voice == "crisp-en"
    assert result.decision.clamped is False
    assert result.speech.voice == "crisp-en"
    assert result.speech.produced_via == "tts:fake"


def test_invalid_backend_is_clamped_to_router_default() -> None:
    agent, _, tts_router = _supervisor(
        _SupervisorChoice(backend="elevenlabs", voice="v", rationale="model hallucinated")
    )
    result = asyncio.run(agent.synthesize(text="t"))
    assert result.decision.clamped is True
    assert result.decision.backend == tts_router.default_backend == "kokoro"
    # Still produced audio despite the invalid pick — delivery guaranteed.
    assert result.speech.produced_via == "tts:fake"


def test_uses_planning_role_and_lists_real_backends_in_prompt() -> None:
    agent, fake_model, _ = _supervisor(_SupervisorChoice(backend="kokoro", voice="v"))
    asyncio.run(agent.synthesize(text="my beat", voice_hint="warm", tone_hint="educational"))
    assert len(fake_model.calls) == 1
    call = fake_model.calls[0]
    assert call.model == "planning-model"
    assert call.system == SYSTEM_PROMPT
    assert call.schema is _SupervisorChoice
    # Both §11 halves: real backends offered to the model + hints threaded in.
    assert "kokoro" in call.prompt and "nvidia" in call.prompt
    assert "warm" in call.prompt
    assert "educational" in call.prompt
    assert "my beat" in call.prompt


def test_fallback_still_guarantees_audio_when_chosen_backend_fails() -> None:
    # Even a *valid* choice whose backend fails must yield audio via the router's
    # deterministic fallback — the supervisor never returns without speech.
    class Failing:
        name = "kokoro"

        async def synthesize(self, *, text: str, voice: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    fake_model = FakeProvider([_SupervisorChoice(backend="kokoro", voice="v")])
    model_router = ModelRouter(
        providers={"fake": fake_model},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    tts_router = TTSRouter(
        providers={"kokoro": Failing(), "nvidia": FakeTTSProvider()},
        fallback_order=["kokoro", "nvidia"],
    )
    agent = TTSSupervisorAgent(model_router, tts_router)
    result = asyncio.run(agent.synthesize(text="t"))
    assert result.decision.backend == "kokoro"  # the agent's pick is preserved
    assert result.speech.produced_via == "tts:fake"  # nvidia delivered via fallback
