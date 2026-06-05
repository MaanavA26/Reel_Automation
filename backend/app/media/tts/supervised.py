"""Adapter that presents a `TTSSupervisorAgent` as a plain `TTSProvider`.

The media pipeline (`app.media.pipeline.MediaPipeline`) consumes a single
deterministic `TTSProvider` — it calls ``tts.synthesize(*, text, voice)`` and
expects a `SynthesizedSpeech` back (CLAUDE.md §4: the pipeline is a tool, it does
not orchestrate model judgment). The TTS *fabric*, by contrast, is two collaborators:

* the `TTSSupervisorAgent` — the *judgment* half (CLAUDE.md §4), which asks the
  ``PLANNING`` model to pick the backend + voice best suited to a beat, and
* the `TTSRouter` — the deterministic half it delegates to, which executes the
  synthesis and *guarantees delivery* via ordered fallback.

The supervisor's own ``synthesize`` has a richer signature (``voice_hint`` /
``tone_hint``) and returns a `SupervisedSpeech` (the audio *plus* the
`TTSDecision` provenance). This thin adapter bridges the two contracts without
touching either: it satisfies the structural `TTSProvider` protocol so the
pipeline's seam is unchanged, forwards the per-call ``voice`` as the channel
voice hint, and unwraps the supervisor's result to the bare `SynthesizedSpeech`.
The decision provenance is intentionally dropped here (the media plan records
only the produced audio); a future surface that wants the `TTSDecision` can call
the supervisor directly.

Pure wiring glue — no network, no model call of its own — so it lives beside the
fabric it adapts rather than in the composition root, and is unit-testable with a
fake supervisor.
"""

from __future__ import annotations

from app.agents.tts_supervisor import TTSSupervisorAgent
from app.media.schemas import SynthesizedSpeech

PROVIDER_NAME = "supervised"


class SupervisedTtsProvider:
    """Presents a `TTSSupervisorAgent` as a `TTSProvider` for the media pipeline.

    Holds the supervisor and an optional ``tone_hint`` (a channel/brand tone the
    composition root can fix at wiring time; ``None`` lets the model decide). Each
    ``synthesize`` call forwards the requested ``voice`` as the supervisor's
    ``voice_hint`` and returns the produced `SynthesizedSpeech`, discarding the
    `TTSDecision` provenance the pipeline has no slot for.
    """

    name = PROVIDER_NAME

    def __init__(self, supervisor: TTSSupervisorAgent, *, tone_hint: str | None = None) -> None:
        self._supervisor = supervisor
        self._tone_hint = tone_hint

    async def synthesize(self, *, text: str, voice: str) -> SynthesizedSpeech:
        """Synthesize ``text`` via the supervised router; return the audio only.

        The per-call ``voice`` is passed as the channel voice hint (the model may
        refine it); delivery is guaranteed by the router's ordered fallback. The
        `SupervisedSpeech.decision` provenance is dropped — the media plan records
        only the produced `SynthesizedSpeech`.
        """
        supervised = await self._supervisor.synthesize(
            text=text,
            voice_hint=voice,
            tone_hint=self._tone_hint,
        )
        return supervised.speech
