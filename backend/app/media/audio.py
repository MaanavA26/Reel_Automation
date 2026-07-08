"""Shared pure audio primitives: WAV PCM16 decode, silence, and splicing.

Extracted from `SegmentedTTSProvider` (#150 / ADR 0064) so the per-beat
narration synthesizer (`app.media.narration`, ADR 0067) reuses the *identical*
decode/silence/splice math instead of duplicating it. The functions here are
byte-for-byte the behavior `segmented.py` shipped; that module now imports
them (its public API and error contract are unchanged — it normalizes this
module's `AudioProcessingError` to its own `SegmentedTtsError`).

Scope discipline (CLAUDE.md §4/§7): everything here is deterministic *tool*
code — stdlib `wave`/`array` only, no numpy, no model, no judgment. The
matching **encoder** (`encode_wav_pcm16`) and the exact duration helper
(`duration_ms_from_samples`) deliberately stay in `app.media.tts.kokoro`,
their original home — they are already shared, pure functions with their own
error type (`KokoroTtsError`) and callers/tests; re-homing them would churn
public imports for zero behavioral gain. This module holds only the pieces
that previously lived as `segmented.py` privates.

The decode contract mirrors `encode_wav_pcm16` precisely: each int16 frame is
divided by ``32767.0`` (the same constant the encoder multiplies by), so a
decode → splice → re-encode round trip introduces no *additional* clipping or
precision loss beyond the int16 quantization the source WAV already carries
(the ADR 0064 "amplitude fidelity" invariant, locked by tests).
"""

from __future__ import annotations

import array
import io
import wave
from collections.abc import Sequence

from app.media.composition.ffmpeg import CompositionError, resolve_local_path

#: A natural short breath-gap length in milliseconds — the shared default for
#: both intra-beat sentence gaps (`SegmentedTTSProvider`, ADR 0064) and
#: inter-beat gaps (`NarrationSynthesizer`, ADR 0067). A reasonable starting
#: point, not a scientifically derived optimum — kept as a named constant so
#: it is easy to tune later once real renders are evaluated for naturalness.
#: (Moved here from `segmented.py`, which re-exports it unchanged.)
DEFAULT_PAUSE_MS = 300


class AudioProcessingError(RuntimeError):
    """Raised when a WAV clip cannot be decoded/spliced by these primitives.

    One local error type for this module's own contract failures (undecodable
    bytes, a non-mono/16-bit clip, disagreeing sample rates, an unresolvable
    clip URI), mirroring `KokoroTtsError`/`CompositionError`. Callers that own
    a seam-specific error type (`SegmentedTtsError`, `NarrationError`)
    normalize this to theirs at their boundary.
    """


def decode_wav_pcm16(audio_bytes: bytes) -> tuple[list[float], int]:
    """Decode mono 16-bit PCM WAV bytes to floats in ``[-1, 1]`` + sample rate.

    Pure, stdlib-only (`wave` + `array`) — the precise inverse of
    `encode_wav_pcm16`: each int16 frame is divided by ``32767.0`` (the same
    constant the encoder scales by), so round-tripping introduces no
    *additional* clipping or precision loss beyond the int16 quantization
    already baked into the source WAV. Requires mono/16-bit input (what every
    in-repo `encode_wav_pcm16` caller emits); raises `AudioProcessingError` on
    any other WAV shape or an undecodable blob, rather than silently
    misreading the samples.
    """
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_in:
            if wav_in.getnchannels() != 1 or wav_in.getsampwidth() != 2:
                raise AudioProcessingError(
                    "expected a mono 16-bit PCM WAV clip, got "
                    f"{wav_in.getnchannels()} channel(s) at {wav_in.getsampwidth() * 8}-bit"
                )
            sample_rate = wav_in.getframerate()
            raw_frames = wav_in.readframes(wav_in.getnframes())
    except (wave.Error, EOFError) as exc:
        # `wave.open` raises bare `EOFError` (not `wave.Error`) on empty or
        # truncated bytes — normalize both so no stdlib error type escapes
        # this module's contract.
        raise AudioProcessingError(f"could not decode the clip as WAV: {exc}") from exc

    pcm = array.array("h")  # signed 16-bit, matches encode_wav_pcm16's output
    pcm.frombytes(raw_frames)
    samples = [value / 32767.0 for value in pcm]
    return samples, sample_rate


def silence_sample_count(duration_ms: int, sample_rate: int) -> int:
    """Sample count of a ``duration_ms`` silence at ``sample_rate``. Pure.

    The single rounding rule (``round(ms * rate / 1000)``) both
    `splice_with_pauses`'s gap insertion and `NarrationSynthesizer`'s exact
    offset math use — one source of truth, so construction-time cue offsets
    can never disagree with where the splice actually put the gaps.
    """
    return round(duration_ms * sample_rate / 1000)


def make_silence(duration_ms: int, sample_rate: int) -> list[float]:
    """A ``duration_ms``-long run of zero samples at ``sample_rate``. Pure."""
    return [0.0] * silence_sample_count(duration_ms, sample_rate)


def splice_with_pauses(
    clips: Sequence[tuple[list[float], int]], pause_ms: int
) -> tuple[list[float], int]:
    """Concatenate decoded clips with a fixed silence gap between them. Pure.

    Requires every clip to share one sample rate (expected — one provider +
    one voice produced all of them); a mismatch raises `AudioProcessingError`
    rather than silently resampling. The gap is inserted between every pair of
    clips only (never before the first or after the last).
    """
    if not clips:
        raise AudioProcessingError("no decoded clips to splice")

    sample_rate = clips[0][1]
    rates = {rate for _, rate in clips}
    if len(rates) > 1:
        raise AudioProcessingError(
            f"per-clip sample rates disagree, cannot concatenate: {sorted(rates)!r}"
        )

    gap = make_silence(pause_ms, sample_rate)
    combined: list[float] = []
    for index, (samples, _) in enumerate(clips):
        if index > 0:
            combined.extend(gap)
        combined.extend(samples)
    return combined, sample_rate


def read_wav_clip(audio_uri: str) -> tuple[list[float], int]:
    """Resolve + read a clip's URI and decode it to PCM samples + sample rate.

    Reuses `resolve_local_path` (the same ``file://``-or-bare-path URI
    convention the ffmpeg composition adapter and `AeneasAligner` use) rather
    than reimplementing it, normalizing its `CompositionError` to this
    module's own error type. Deterministic file I/O — the one non-pure
    function here, kept beside the decode it feeds. A missing or unreadable
    file is normalized the same way (`AudioProcessingError`, original error
    chained) — no raw `FileNotFoundError`/`OSError` escapes this module's
    contract.
    """
    try:
        path = resolve_local_path(audio_uri)
    except CompositionError as exc:
        raise AudioProcessingError(str(exc)) from exc
    try:
        audio_bytes = path.read_bytes()
    except OSError as exc:
        raise AudioProcessingError(f"could not read audio clip at {audio_uri!r}: {exc}") from exc
    return decode_wav_pcm16(audio_bytes)
