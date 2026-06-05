"""TTS Supervisor agent — chooses the backend + voice best suited to a beat.

This is genuine *judgment* (CLAUDE.md §4): given the narration text and an
optional channel voice/tone hint, the supervisor asks the ``PLANNING``-role model
to *select* which TTS backend and which voice will best render the beat — a
quality call, not a deterministic transform. It then hands that choice to the
deterministic `TTSRouter`, which executes synthesis and *guarantees delivery* via
ordered fallback. So: **agent proposes, router disposes + guarantees output.**

It owns no provider-specific code and never synthesizes audio itself (that is the
provider tool's job). To keep the agent honest rather than theater, both halves
of the §11 index/validate pattern are enforced:

- the model is told its *real* options — the backend names actually registered
  with the router are listed in the prompt, and
- the model's response is validated against that same set and, if it names an
  unknown backend, **clamped to the router's default** (the cheapest/most-local
  one). The deterministic fallback then guarantees output regardless.

The model returns a transient `_SupervisorChoice` DTO (backend + voice +
rationale). This agent re-exposes the validated decision as `TTSDecision`
(adding a ``clamped`` provenance flag) alongside the produced `SynthesizedSpeech`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from app.media.schemas import SynthesizedSpeech
from app.media.tts.router import TTSRouter
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter

SYSTEM_PROMPT = (
    "You are a text-to-speech direction specialist for a short-form video "
    "engine. Given a narration beat and the set of available TTS backends, "
    "choose the single backend and a voice identifier best suited to render the "
    "beat well. Honor any channel voice/tone hint. You MUST pick a backend from "
    "the provided available list — do not invent backend names. Keep the voice a "
    "short identifier string. Briefly justify the choice. You only select; you "
    "do not synthesize audio."
)


class _SupervisorChoice(BaseModel):
    """Structured output the ``PLANNING`` model returns (transient DTO).

    Model-authored selection only — no ids, no audio. The chosen ``backend`` is
    validated against the router's real backend set by the agent and clamped to
    the default if unknown; the model never gets the last word on execution.
    """

    backend: str
    voice: str
    rationale: str | None = None


@dataclass(frozen=True)
class TTSDecision:
    """The validated backend/voice decision plus its provenance.

    ``clamped`` records whether the model's proposed backend was invalid and
    replaced with the router default — the auditable signal that the deterministic
    safety net engaged, distinguishing a genuine model pick from a fallback.
    """

    backend: str
    voice: str
    rationale: str | None
    clamped: bool


@dataclass(frozen=True)
class SupervisedSpeech:
    """The supervisor's guaranteed output: the audio plus the decision behind it."""

    speech: SynthesizedSpeech
    decision: TTSDecision


class TTSSupervisorAgent:
    """Selects backend + voice via the model, then synthesizes via the router.

    The ``model_router`` resolves the ``PLANNING``-role LLM (the *judgment* call);
    the ``tts_router`` is the deterministic TTS fabric that executes + falls back
    (the *guarantee*). The two seams are named so the agent-vs-tool boundary is
    visible at a glance.
    """

    def __init__(self, model_router: ModelRouter, tts_router: TTSRouter) -> None:
        self._model_router = model_router
        self._tts_router = tts_router

    async def synthesize(
        self,
        *,
        text: str,
        voice_hint: str | None = None,
        tone_hint: str | None = None,
        avoid_backends: Iterable[str] = (),
    ) -> SupervisedSpeech:
        """Choose a backend + voice for ``text`` and synthesize it (with fallback).

        Asks the ``PLANNING`` model to select among the router's *real* backends,
        validates/clamps the choice, then delegates to `TTSRouter.synthesize`,
        which guarantees audio via ordered fallback. Returns the produced speech
        together with the `TTSDecision` provenance.

        ``avoid_backends`` is an optional set of backend names a prior attempt
        already produced unsatisfactory audio with (the QA re-synthesis loop of
        ADR 0052 passes the backends it has tried). It is threaded into the prompt
        as guidance so the model picks *differently* on a retry — steering the
        judgment, not constraining the router. It is advisory only: the model may
        still re-pick an avoided backend (and the deterministic fallback may land
        on one regardless), so this never narrows the router's real options. The
        default empty set preserves the original single-shot behavior exactly.
        """
        available = self._tts_router.available()
        choice = await self._model_router.for_role(ModelRole.PLANNING).complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(text, available, voice_hint, tone_hint, avoid_backends),
            schema=_SupervisorChoice,
        )
        decision = self._validate(choice)
        speech = await self._tts_router.synthesize(
            text=text, voice=decision.voice, backend=decision.backend
        )
        return SupervisedSpeech(speech=speech, decision=decision)

    def _validate(self, choice: _SupervisorChoice) -> TTSDecision:
        """Clamp an out-of-set backend to the router default (§11 validate half)."""
        available = self._tts_router.available()
        if choice.backend in available:
            return TTSDecision(
                backend=choice.backend,
                voice=choice.voice,
                rationale=choice.rationale,
                clamped=False,
            )
        return TTSDecision(
            backend=self._tts_router.default_backend,
            voice=choice.voice,
            rationale=choice.rationale,
            clamped=True,
        )

    @staticmethod
    def _build_prompt(
        text: str,
        available: Iterable[str],
        voice_hint: str | None,
        tone_hint: str | None,
        avoid_backends: Iterable[str] = (),
    ) -> str:
        backends = ", ".join(sorted(available))
        hint_lines = []
        if voice_hint:
            hint_lines.append(f"Preferred channel voice hint: {voice_hint}")
        if tone_hint:
            hint_lines.append(f"Desired narrative tone: {tone_hint}")
        avoid = sorted(set(avoid_backends))
        if avoid:
            # Advisory steer for a QA retry: name the backends a prior attempt
            # produced unsatisfactory audio with so the model picks differently.
            # Not a hard constraint — the router's real option set is unchanged.
            hint_lines.append(
                "A previous attempt produced unsatisfactory audio with backend(s): "
                f"{', '.join(avoid)}. Prefer a different backend and/or voice this time."
            )
        hints = ("\n".join(hint_lines) + "\n\n") if hint_lines else ""
        return (
            f"Available TTS backends: {backends}\n\n"
            f"{hints}"
            f"Narration beat to voice:\n{text}\n\n"
            "Choose the backend and voice."
        )
