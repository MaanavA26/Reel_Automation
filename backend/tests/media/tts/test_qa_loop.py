"""Tests for the bounded QA-gated TTS re-synthesis loop (ADR 0052).

Hermetic end-to-end over the real units: a `FakeProvider`-backed `ModelRouter`
scripts the supervisor's backend/voice choices, a `TTSRouter` over fixed-duration
fake providers executes them, and the real `TTSQualityService` gates the output.
Each fake backend returns a *fixed* duration so a test can place an attempt
inside or outside the QA tolerance band deterministically and assert the loop's
re-routing, bounding, and best-effort behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.tts_supervisor import TTSSupervisorAgent, _SupervisorChoice
from app.media.schemas import SynthesizedSpeech
from app.media.tts.base import FakeTTSProvider
from app.media.tts.qa import TTSQACheck, TTSQualityService
from app.media.tts.qa_loop import QAGatedSpeech, TTSQALoop
from app.media.tts.router import TTSRouter
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter


class FixedDurationProvider:
    """A `TTSProvider` whose clips always have a fixed ``duration_ms`` (hermetic).

    Unlike `FakeTTSProvider` (duration derived from text length), this returns a
    constant duration so a test can pin a backend inside/outside the QA band.
    """

    def __init__(self, name: str, duration_ms: int) -> None:
        self.name = name
        self._duration_ms = duration_ms

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        return SynthesizedSpeech(
            audio_uri=f"fake://{self.name}/{voice}.wav",
            duration_ms=self._duration_ms,
            voice=voice,
            produced_via=f"tts:{self.name}",
        )


def _loop(
    choices: list[_SupervisorChoice],
    backends: dict[str, int],
    *,
    fallback_order: list[str],
    max_attempts: int = 3,
    words_per_minute: float = 150.0,
    duration_tolerance: float = 0.5,
) -> tuple[TTSQALoop, FakeProvider]:
    fake_model = FakeProvider(choices)
    model_router = ModelRouter(
        providers={"fake": fake_model},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    tts_router = TTSRouter(
        providers={name: FixedDurationProvider(name, ms) for name, ms in backends.items()},
        fallback_order=fallback_order,
    )
    supervisor = TTSSupervisorAgent(model_router, tts_router)
    qa = TTSQualityService(words_per_minute=words_per_minute, duration_tolerance=duration_tolerance)
    return TTSQALoop(supervisor, qa, max_attempts=max_attempts), fake_model


# 30 words at 150 wpm => expected 12_000 ms; tolerance 0.5 => band [6_000, 18_000].
TEXT = "word " * 30


def test_passes_on_first_attempt_no_retry() -> None:
    loop, fake_model = _loop(
        choices=[_SupervisorChoice(backend="kokoro", voice="warm")],
        backends={"kokoro": 12_000, "nvidia": 1_000},
        fallback_order=["kokoro", "nvidia"],
    )
    result = asyncio.run(loop.synthesize(text=TEXT))
    assert isinstance(result, QAGatedSpeech)
    assert result.report.passed is True
    assert result.attempts == 1
    assert result.decision.backend == "kokoro"
    assert len(fake_model.calls) == 1  # no re-synthesis


def test_reroutes_to_a_different_backend_on_qa_failure() -> None:
    # First pick (kokoro) is truncated (1_000 ms, below band) -> QA fails ->
    # supervisor is asked again and picks nvidia (12_000 ms, in band) -> passes.
    loop, fake_model = _loop(
        choices=[
            _SupervisorChoice(backend="kokoro", voice="v1"),
            _SupervisorChoice(backend="nvidia", voice="v2"),
        ],
        backends={"kokoro": 1_000, "nvidia": 12_000},
        fallback_order=["kokoro", "nvidia"],
    )
    result = asyncio.run(loop.synthesize(text=TEXT))
    assert result.report.passed is True
    assert result.attempts == 2
    assert result.decision.backend == "nvidia"
    assert len(fake_model.calls) == 2
    # The retry threaded the failed backend through as an avoid-steer — the first
    # prompt has no steer, the retry prompt does (the loop→supervisor wiring).
    assert "unsatisfactory" not in fake_model.calls[0].prompt
    assert "unsatisfactory" in fake_model.calls[1].prompt


def test_stops_at_max_attempts_and_returns_best_effort() -> None:
    # Every backend is out of band, so no attempt ever passes QA. The loop must
    # stop at max_attempts and return the attempt closest to expected (12_000 ms):
    #   attempt1 kokoro=1_000 (gap 11_000), attempt2 nvidia=30_000 (gap 18_000),
    #   attempt3 kokoro=1_000 (gap 11_000). Best = the first (tie kept earliest).
    loop, fake_model = _loop(
        choices=[
            _SupervisorChoice(backend="kokoro", voice="v1"),
            _SupervisorChoice(backend="nvidia", voice="v2"),
            _SupervisorChoice(backend="kokoro", voice="v3"),
        ],
        backends={"kokoro": 1_000, "nvidia": 30_000},
        fallback_order=["kokoro", "nvidia"],
        max_attempts=3,
    )
    result = asyncio.run(loop.synthesize(text=TEXT))
    assert result.report.passed is False  # best-effort, not a hard fail
    assert TTSQACheck.DURATION_PLAUSIBLE in result.report.failed_checks
    assert len(fake_model.calls) == 3  # exactly max_attempts syntheses ran
    assert result.attempts == 3  # ``attempts`` reports total syntheses run
    # Best-effort = closest to expected (the 1_000 ms attempt, gap 11_000).
    assert result.speech.duration_ms == 1_000
    assert result.decision.backend == "kokoro"


def test_best_effort_prefers_closest_to_expected_duration() -> None:
    # attempt1 nvidia=30_000 (gap 18_000), attempt2 kokoro=5_000 (gap 7_000).
    # Both out of band [6_000, 18_000]; best-effort keeps the closer one (kokoro).
    loop, _ = _loop(
        choices=[
            _SupervisorChoice(backend="nvidia", voice="v1"),
            _SupervisorChoice(backend="kokoro", voice="v2"),
        ],
        backends={"kokoro": 5_000, "nvidia": 30_000},
        fallback_order=["kokoro", "nvidia"],
        max_attempts=2,
    )
    result = asyncio.run(loop.synthesize(text=TEXT))
    assert result.report.passed is False
    assert result.speech.duration_ms == 5_000
    assert result.decision.backend == "kokoro"
    assert result.attempts == 2


def test_max_attempts_one_runs_once_and_returns_best_effort() -> None:
    loop, fake_model = _loop(
        choices=[_SupervisorChoice(backend="kokoro", voice="v")],
        backends={"kokoro": 1_000, "nvidia": 12_000},
        fallback_order=["kokoro", "nvidia"],
        max_attempts=1,
    )
    result = asyncio.run(loop.synthesize(text=TEXT))
    assert result.report.passed is False
    assert result.attempts == 1
    assert len(fake_model.calls) == 1


def test_invalid_max_attempts_rejected() -> None:
    fake_model = FakeProvider([])
    model_router = ModelRouter(
        providers={"fake": fake_model},
        policy={ModelRole.PLANNING: ModelChoice("fake", "planning-model")},
    )
    tts_router = TTSRouter(providers={"kokoro": FakeTTSProvider()}, fallback_order=["kokoro"])
    supervisor = TTSSupervisorAgent(model_router, tts_router)
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        TTSQALoop(supervisor, TTSQualityService(), max_attempts=0)
