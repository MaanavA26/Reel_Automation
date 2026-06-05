"""Bounded, QA-gated TTS re-synthesis loop (ADR 0052).

The orchestration seam that wires the agent and the tool together: synthesize →
QA → (if QA fails and attempts remain) ask the `TTSSupervisorAgent` to pick a
*different* backend/voice → re-synthesize. It is neither a pure tool nor an agent
— it *coordinates* one of each, so it lives beside the fabric it drives rather
than in `qa.py` (which stays a pure tool) or the supervisor (which stays pure
judgment). This keeps the agent/tool boundary structural (CLAUDE.md §4/§11).

The §11 "model proposes, code decides" shape, made concrete here
----------------------------------------------------------------
* **Judgment (agent):** *which* backend/voice to try next is the
  `TTSSupervisorAgent`'s call. On a retry the loop passes the
  already-tried backends as ``avoid_backends`` so the model steers away from
  what just failed — but it never *forces* a different pick (advisory hint only).
* **Decision (code):** *whether* the audio is acceptable (`TTSQualityService`)
  and *whether to retry at all* (the bounded counter) are deterministic. The
  model gets no vote on either.

Best-effort completes (the synthesis-layer inversion)
-----------------------------------------------------
Mirroring the synthesis/critic contract (a thin-but-valid result completes rather
than hard-fails), the loop **never raises on QA failure**. If every attempt fails
QA, it returns the *best* attempt seen — the one whose actual duration is closest
to the QA-expected duration (the smallest plausibility violation), a deterministic,
showable tie-break — with its (failing) `TTSQAReport` attached so the caller can
see audio was produced but did not pass. A render degrading to the best available
clip beats failing the whole video, exactly as the router degrades to a working
backend. The supervisor's own router fallback still guarantees *some* audio per
attempt; this loop adds the quality gate on top.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from app.agents.tts_supervisor import TTSDecision, TTSSupervisorAgent
from app.media.schemas import SynthesizedSpeech
from app.media.tts.qa import TTSQAReport, TTSQualityService

#: Default cap on total synthesis attempts (initial + retries). Bounds the loop
#: the same way the revision loop's iteration counter bounds re-synthesis (ADR
#: 0012): three lets the supervisor try a couple of alternative backends/voices
#: before the loop gives up and returns best-effort. Must be ``>= 1``.
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class QAGatedSpeech:
    """The loop's guaranteed output: the chosen audio, its QA report, and provenance.

    A frozen dataclass mirroring `SupervisedSpeech`/`TTSDecision` (not a Pydantic
    artifact — `TTSQAReport` is the strict DTO; this is the in-process result
    wrapper). ``report.passed`` tells the caller whether the returned ``speech``
    cleared QA; on exhaustion it is ``False`` and ``speech`` is the best-effort
    pick. ``decision`` is the supervisor's choice behind the returned ``speech``,
    and ``attempts`` is how many syntheses ran (1..max) — auditable provenance.
    """

    speech: SynthesizedSpeech
    report: TTSQAReport
    decision: TTSDecision
    attempts: int


class TTSQALoop:
    """Runs synth → QA → supervised re-synth, bounded, returning best-effort.

    Holds the `TTSSupervisorAgent` (judgment + guaranteed-delivery router) and the
    `TTSQualityService` (deterministic gate). ``max_attempts`` caps total
    syntheses. The agent/tool seam is visible in the constructor: a named agent
    and a named tool, coordinated by deterministic loop code.
    """

    def __init__(
        self,
        supervisor: TTSSupervisorAgent,
        qa: TTSQualityService,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._supervisor = supervisor
        self._qa = qa
        self._max_attempts = max_attempts

    async def synthesize(
        self,
        *,
        text: str,
        voice_hint: str | None = None,
        tone_hint: str | None = None,
    ) -> QAGatedSpeech:
        """Synthesize ``text`` with QA-gated, bounded supervised re-synthesis.

        Loops up to ``max_attempts``: the supervisor picks a backend/voice and
        synthesizes (its router guarantees audio), then QA gates the clip. On a
        QA pass the result is returned immediately. On failure with attempts
        remaining, the tried backend is added to ``avoid_backends`` and the
        supervisor is asked again (steered to pick differently). If every attempt
        fails QA, the best-effort attempt (closest to expected duration) is
        returned with its failing report — never raises on QA failure.
        """
        tried_backends: list[str] = []
        best: QAGatedSpeech | None = None

        for attempt in range(1, self._max_attempts + 1):
            supervised = await self._supervisor.synthesize(
                text=text,
                voice_hint=voice_hint,
                tone_hint=tone_hint,
                avoid_backends=tried_backends,
            )
            report = self._qa.check(supervised.speech, text=text)
            candidate = QAGatedSpeech(
                speech=supervised.speech,
                report=report,
                decision=supervised.decision,
                attempts=attempt,
            )
            if report.passed:
                return candidate

            best = self._better(best, candidate)
            # Steer the next pick away from the backend the model just chose
            # (the pick it controls), not whichever the router fell back to.
            tried_backends.append(supervised.decision.backend)

        # Exhausted: every attempt failed QA. ``best`` is set (the loop ran at
        # least once), so return the closest-to-expected clip, report attached.
        # ``attempts`` reports the *total* syntheses run (== max_attempts here),
        # not the origin attempt of the winning clip — the useful provenance
        # signal is "how many tries did the loop take", per `QAGatedSpeech`.
        assert best is not None  # max_attempts >= 1 guarantees one iteration
        return dataclasses.replace(best, attempts=self._max_attempts)

    @staticmethod
    def _better(current: QAGatedSpeech | None, candidate: QAGatedSpeech) -> QAGatedSpeech:
        """Keep whichever attempt's duration is closest to the QA-expected duration.

        Deterministic best-effort tie-break: the smaller absolute gap between the
        clip's actual ``duration_ms`` and ``report.expected_duration_ms`` wins.
        On an exact tie the incumbent is kept (the earlier attempt) so the choice
        is stable. (The exhaustion return overrides ``attempts`` to the total
        synthesis count — see ``synthesize`` — so the kept clip's own attempt
        number here is not the value the caller ultimately sees.)
        """
        if current is None:
            return candidate
        current_gap = abs(current.speech.duration_ms - current.report.expected_duration_ms)
        candidate_gap = abs(candidate.speech.duration_ms - candidate.report.expected_duration_ms)
        return candidate if candidate_gap < current_gap else current
