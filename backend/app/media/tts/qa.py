"""Deterministic quality-assurance for synthesized speech (a tool, ADR 0052).

After a `TTSProvider` returns a `SynthesizedSpeech`, this service inspects the
descriptor and reports whether the clip is *plausibly usable* before it is laid
under captions and composed. Per CLAUDE.md §4 this is pure **tool/service** work:
every check is a deterministic comparison over existing descriptor fields — no
LLM, no judgment. The *judgment* half (what backend/voice to try if QA fails)
lives in the `TTSSupervisorAgent`; the bounded loop that wires the two together
is `app.media.tts.qa_loop` (model proposes, code decides — CLAUDE.md §11).

What this can check now (descriptor-level, hermetic)
----------------------------------------------------
The synthesis layer "traffics in descriptors, not bytes" (ADR 0019), so the
checks here are exactly those validatable from a `SynthesizedSpeech` without
opening the audio:

* **empty audio** — ``duration_ms == 0`` (the synth produced nothing usable);
* **duration plausibility** — the actual ``duration_ms`` is within a tolerance
  band of an *expected* duration derived from the source ``text`` via a words
  -per-minute speech-rate model. This is the load-bearing check: it catches
  truncated audio (far too short) and runaway/looping audio (far too long).
  The expected duration is derived from the **text**, never from caption timing
  — caption timings are allocated *from* ``audio.duration_ms`` upstream
  (`pipeline._allocate_timings`), so comparing audio to captions is circular and
  always passes. Word count vs. a rate model is the only non-tautological
  reference, and the one that actually catches a bad clip.
* **sane metadata** — ``audio_uri`` and ``voice`` are non-empty (a descriptor
  pointing nowhere, or with no voice recorded, is not a usable artifact).

Explicitly deferred — needs the real audio bytes (NOT done here)
----------------------------------------------------------------
Waveform-level checks — **silence detection** (is the clip audible, or did the
backend emit a silent buffer of the right length?), clipping, and true measured
duration via ``ffprobe`` — require the actual PCM/encoded bytes plus ffmpeg/an
audio decoder. None of that is available in the offline build sandbox (no
ffmpeg, no real Kokoro model), so it **cannot be validated here** and is *not*
implemented (a Protocol seam with only a fake and no real backend would be its
own speculative overbuild — §7). It is named as deferred in ADR 0052 §Deferred;
when real-audio QA lands it becomes a sibling waveform-QA service behind the same
`TTSQAReport`, gated by ``@pytest.mark.integration``. A descriptor-level PASS
here therefore means "plausibly usable by its metadata", **not** "verified
audible" — see ADR 0052 for that last-mile caveat.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.media.schemas import SynthesizedSpeech, _gen_id

#: Default narration speech rate (words per minute) used to derive the expected
#: clip duration from the source text. ~150 wpm is a common conversational
#: short-form pace; tunable per `TTSQualityService` instance. This is a *plausibility*
#: reference for QA, not a claim about any specific backend's true rate — the wide
#: default tolerance below absorbs normal pacing variation.
DEFAULT_WORDS_PER_MINUTE: float = 150.0

#: Default fractional tolerance around the expected duration (±60%). Deliberately
#: wide: the goal is to catch *gross* failures (truncation, runaway/looping,
#: empty), not to police natural pacing differences between backends/voices. A
#: tighter band would flag healthy clips; a real WPM is text- and voice-dependent.
DEFAULT_DURATION_TOLERANCE: float = 0.6


class TTSQACheck(StrEnum):
    """The deterministic, descriptor-level QA checks this tool performs.

    Each maps to one `TTSCheckResult` in a report. Waveform-level checks
    (silence, clipping) are intentionally absent — they need real bytes and are
    deferred (see the module docstring and ADR 0052).
    """

    NON_EMPTY_AUDIO = "non_empty_audio"
    DURATION_PLAUSIBLE = "duration_plausible"
    SANE_METADATA = "sane_metadata"


class TTSCheckResult(BaseModel):
    """The outcome of a single QA check — pass/fail plus a human-readable detail.

    Strict (`extra='forbid'`). ``detail`` always explains the verdict (the
    observed value vs. the expectation) so a failing report is self-describing
    for logs and the re-synthesis loop's provenance.
    """

    model_config = ConfigDict(extra="forbid")

    check: TTSQACheck
    passed: bool
    detail: str


class TTSQAReport(BaseModel):
    """The structured verdict of a `TTSQualityService` pass over one clip.

    A strict, id-prefixed (`qa_`) artifact mirroring the rest of the media layer's
    DTOs, carrying a required `produced_via` provenance (e.g. ``"tts-qa:descriptor"``)
    so a report always records *which* QA tool produced it. ``passed`` is the
    code-derived overall verdict: ``True`` iff **every** check passed (AND over the
    per-check results) — the model never gets a vote on it. The per-check
    breakdown is preserved so a caller can see *why* a clip was rejected.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _gen_id("qa"))
    speech_id: str
    passed: bool
    checks: list[TTSCheckResult]
    expected_duration_ms: int = Field(ge=0)
    produced_via: str

    @property
    def failed_checks(self) -> list[TTSQACheck]:
        """The checks that failed — the loop's signal for whether to re-synthesize."""
        return [c.check for c in self.checks if not c.passed]


def expected_duration_ms(text: str, *, words_per_minute: float) -> int:
    """Expected clip duration in integer ms from word count and a speech rate. Pure.

    ``words = len(text.split())`` (whitespace-delimited; punctuation rides along,
    which is fine for a plausibility band). Duration = ``words / wpm`` minutes →
    ms. Empty/whitespace text yields ``0`` (an empty script should produce no
    audio). ``words_per_minute`` must be positive.
    """
    if words_per_minute <= 0:
        raise ValueError(f"words_per_minute must be positive, got {words_per_minute}")
    words = len(text.split())
    if words == 0:
        return 0
    return round(words / words_per_minute * 60_000)


class TTSQualityService:
    """Deterministic, descriptor-level QA over `SynthesizedSpeech` (no LLM).

    Pure tool: `check` derives an expected duration from the source text (a WPM
    model) and runs the descriptor-level checks (non-empty audio, duration
    plausibility, sane metadata), returning a `TTSQAReport` whose overall verdict
    is the AND of the per-check results. Hermetic — touches no audio bytes,
    network, or model.

    ``words_per_minute`` and ``duration_tolerance`` are configurable so a wiring
    root can tune the plausibility band per channel/voice without changing code.
    """

    name = "descriptor"

    def __init__(
        self,
        *,
        words_per_minute: float = DEFAULT_WORDS_PER_MINUTE,
        duration_tolerance: float = DEFAULT_DURATION_TOLERANCE,
    ) -> None:
        if words_per_minute <= 0:
            raise ValueError(f"words_per_minute must be positive, got {words_per_minute}")
        if duration_tolerance < 0:
            raise ValueError(f"duration_tolerance must be non-negative, got {duration_tolerance}")
        self._wpm = words_per_minute
        self._tolerance = duration_tolerance

    def check(self, speech: SynthesizedSpeech, *, text: str) -> TTSQAReport:
        """Run the descriptor-level QA checks over ``speech`` produced from ``text``.

        ``text`` is the narration the clip *should* contain — the non-circular
        reference for the duration plausibility band (see the module docstring).
        Returns a `TTSQAReport`; ``report.passed`` is ``True`` iff every check
        passed.
        """
        expected_ms = expected_duration_ms(text, words_per_minute=self._wpm)
        checks = [
            self._check_non_empty_audio(speech),
            self._check_duration_plausible(speech, expected_ms),
            self._check_sane_metadata(speech),
        ]
        return TTSQAReport(
            speech_id=speech.id,
            passed=all(c.passed for c in checks),
            checks=checks,
            expected_duration_ms=expected_ms,
            produced_via=f"tts-qa:{self.name}",
        )

    @staticmethod
    def _check_non_empty_audio(speech: SynthesizedSpeech) -> TTSCheckResult:
        passed = speech.duration_ms > 0
        return TTSCheckResult(
            check=TTSQACheck.NON_EMPTY_AUDIO,
            passed=passed,
            detail=(
                f"duration_ms={speech.duration_ms}"
                + ("" if passed else " — empty/zero-length audio")
            ),
        )

    def _check_duration_plausible(
        self, speech: SynthesizedSpeech, expected_ms: int
    ) -> TTSCheckResult:
        """Actual duration within ``±tolerance`` of the text-derived expectation.

        Skipped (auto-pass) when ``expected_ms == 0`` (empty text): an empty
        script has no plausible duration to compare against, and the non-empty
        -audio check already covers the "produced nothing" case from the other
        side. The band is symmetric and *inclusive* of the bounds.
        """
        if expected_ms == 0:
            return TTSCheckResult(
                check=TTSQACheck.DURATION_PLAUSIBLE,
                passed=True,
                detail="no expected duration (empty text) — duration check skipped",
            )
        low = round(expected_ms * (1 - self._tolerance))
        high = round(expected_ms * (1 + self._tolerance))
        passed = low <= speech.duration_ms <= high
        return TTSCheckResult(
            check=TTSQACheck.DURATION_PLAUSIBLE,
            passed=passed,
            detail=(
                f"duration_ms={speech.duration_ms}, "
                f"expected≈{expected_ms} (tolerated [{low}, {high}])"
                + ("" if passed else " — outside tolerance band")
            ),
        )

    @staticmethod
    def _check_sane_metadata(speech: SynthesizedSpeech) -> TTSCheckResult:
        """``audio_uri`` and ``voice`` are non-empty (a usable descriptor)."""
        missing = [
            field
            for field, value in (("audio_uri", speech.audio_uri), ("voice", speech.voice))
            if not value.strip()
        ]
        passed = not missing
        return TTSCheckResult(
            check=TTSQACheck.SANE_METADATA,
            passed=passed,
            detail=(
                "audio_uri and voice present"
                if passed
                else f"missing/blank metadata field(s): {', '.join(missing)}"
            ),
        )
