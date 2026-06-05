# ADR 0052: TTS QA loop with supervised re-synthesis

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

The agent-supervised TTS fabric (ADR 0049/0050) *guarantees delivery*: the
`TTSSupervisorAgent` picks a backend + voice and the `TTSRouter` falls back until
some backend produces audio. But "produced audio" is not "produced *usable*
audio" — a backend can return a truncated clip, a runaway/looping clip, an
empty buffer, or a descriptor pointing nowhere, and the pipeline would compose it
anyway. There is no quality gate after synthesis.

We want the same correctness shape the Knowledge Reasoning band already uses: a
**deterministic check** that decides accept/retry, and a **bounded loop** that,
on failure, asks the existing *judgment* agent to try something different — model
proposes, code decides (CLAUDE.md §11; the bounded revision loop of ADR 0012).

The hard constraint is honesty about *what is checkable now*. The media layer
"traffics in descriptors, not bytes" (ADR 0019), and the offline build sandbox
has no ffmpeg and no real Kokoro model. So waveform-level QA (is the clip
audible? silence detection, clipping, true measured duration) cannot be built or
validated here without becoming untestable theater.

## Decision

Ship three cleanly separated units, mirroring ADR 0049's agent/tool split.

### 1. `TTSQualityService` — the deterministic gate (a tool)

`backend/app/media/tts/qa.py`. No LLM, no judgment. Given a `SynthesizedSpeech`
and the source `text`, it runs descriptor-level checks and returns a strict,
id-prefixed (`qa_`) `TTSQAReport` (`extra='forbid'`, required `produced_via =
"tts-qa:descriptor"`) with a per-check pass/fail breakdown. The overall
`passed` verdict is **code-derived** — the AND of every check — never modeled.

Checks (exactly those validatable from a descriptor, hermetically):

- **non-empty audio** — `duration_ms == 0` is a fail.
- **duration plausibility** — actual `duration_ms` within `±tolerance` (default
  ±60%) of an *expected* duration derived from the source text via a
  words-per-minute model (default 150 wpm). This is the load-bearing check: it
  catches truncation (far too short) and runaway/looping (far too long).
- **sane metadata** — `audio_uri` and `voice` are non-empty.

**The non-circular-reference decision.** The expected duration is derived from
the **text**, never from caption timing. Upstream, captions are allocated *from*
`audio.duration_ms` (`pipeline._allocate_timings`), so the invariant
`cues[-1].end_ms == audio.duration_ms` holds by construction — comparing audio
duration to caption timing is a tautology that always passes. Word count vs. a
rate model is the only reference that can actually fail, and the one that catches
a bad clip. Rate and tolerance are configurable so a wiring root can tune the
plausibility band per channel/voice.

### 2. `TTSSupervisorAgent.avoid_backends` — an additive steer (the agent)

`backend/app/agents/tts_supervisor.py`. `synthesize` gains one optional param,
`avoid_backends: Iterable[str] = ()`, threaded into the prompt: "a previous
attempt produced unsatisfactory audio with backend(s) X — prefer a different
backend/voice." It is **advisory** (the model may still re-pick; the router's
real option set is unchanged) and **additive** (the empty default reproduces the
ADR 0049 single-shot behavior exactly — all prior supervisor tests stay green).
This is the only approach that actually works: a real `PLANNING` model has no
signal to vary its pick without being told what failed, so a bare "call it again"
would be the theater the supervisor's docstring already swears off.

### 3. `TTSQALoop` — the coordinator (neither tool nor agent)

`backend/app/media/tts/qa_loop.py`. It coordinates one agent + one tool, so it
lives beside the fabric, keeping `qa.py` a pure tool and the supervisor pure
judgment. It loops up to `max_attempts` (default 3): supervisor synthesizes → QA
gates → on a pass, return immediately; on a fail with attempts left, add the
model's chosen backend to `avoid_backends` and ask again. The §11 split is
concrete: *which backend to try next* is the agent's judgment; *whether the audio
is acceptable* and *whether to retry* are deterministic code.

**Best-effort completes (the synthesis-layer inversion).** The loop **never
raises on QA failure** (mirroring synthesis/critic, where a thin-but-valid result
completes rather than hard-fails). On exhaustion it returns the best attempt —
the one whose `duration_ms` is closest to the expected duration (smallest
plausibility violation; ties keep the earlier attempt) — with its *failing*
`TTSQAReport` attached. The output is a frozen `QAGatedSpeech` dataclass (speech +
report + decision + attempt count), matching the `SupervisedSpeech`/`TTSDecision`
dataclass precedent rather than minting a new Pydantic artifact.

## Deferred — needs real audio bytes (NOT built here)

Waveform-level QA — **silence/audibility detection**, clipping, and true measured
duration via `ffprobe` — requires the actual PCM/encoded bytes plus ffmpeg or an
audio decoder, none of which exist in the offline sandbox. It is **deliberately
not implemented**: a Protocol seam with only a fake and no real backend would be
its own speculative overbuild (§7). When real-audio QA lands it becomes a sibling
waveform-QA service behind the same `TTSQAReport`, gated by
`@pytest.mark.integration`. **A descriptor-level PASS therefore means "plausibly
usable by its metadata", not "verified audible"** — this is the last-mile caveat,
the same shape as ADR 0049's "documented-not-yet-live" wire contracts.

## Consequences

- The agent-vs-tool seam stays structural: `qa.py` imports no agent/LLM; the loop
  constructor names a `supervisor` (judgment) and a `qa` tool (decision).
- Output is still always produced: per-attempt the router's fallback guarantees
  audio, and the loop's best-effort return guarantees a result even when no
  attempt clears QA — a render degrades to the best clip rather than failing.
- Fully hermetic: tested against a `FakeProvider`-scripted supervisor and
  fixed-duration fake backends; no network/keys/ffmpeg/real model.
- **Scope/deferral (mirrors ADR 0049 "capability, not wiring"):** this ships the
  QA loop as a standalone capability. Threading it into `MediaPipeline.build` is a
  follow-up; `pipeline.py`, `composition.py`, `core/config.py`, `main.py`, and
  `__init__.py` are untouched, so the parallel composition-root edits union-merge
  cleanly. The supervisor change is the one additive edit to an existing file.
