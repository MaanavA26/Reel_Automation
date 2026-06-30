"""Shared loudness-mastering primitives for the composition layer.

Short-form platforms play audio at a competitive **integrated loudness** target
(roughly -14 LUFS). A raw text-to-speech narration lands well under that — the
first real render measured **-22 LUFS** (issue #128) — so it is mastered with
ffmpeg's two-pass ``loudnorm`` filter before it reaches the muxer.

Per CLAUDE.md §4 this is a deterministic **tool**, never an agent: there is no
judgment here, only the fixed Definition-of-Done loudness target and the parsing
of ffmpeg's analysis output. It is the single source of these primitives so the
render path (ADR 0058) and the downstream QC gate (issue #126) import the *same*
target constants, measure-pass argv, parser, and typed stats model — never two
drifting copies (council coupling C1/C2).

The split mirrors the rest of the composition/probe layers (ADR 0023):

* **Command construction** (`build_loudnorm_measure_args`) and **output parsing**
  (`parse_loudnorm_stats`) are pure, deterministic functions — assertable with
  **no ffmpeg binary present**. They create no temp files and touch no I/O.
* **Execution** (running the measure pass, feeding the stats into pass two) lives
  in the renderer's subprocess seam (`ffmpeg.py`), off the event loop.

Two-pass ``loudnorm`` is the accurate mode: pass one analyses the source and
prints its measured stats as JSON; pass two re-runs the filter with those
measurements supplied as ``measured_*`` parameters plus ``linear=true``, so the
normalization is a single linear gain to the target rather than a dynamic
(and audibly pumping) one-pass correction.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_STRICT = ConfigDict(extra="forbid")

#: Definition-of-Done loudness target (issue #128). These are the platform-
#: competitive values both the render path and the QC gate (issue #126) measure
#: against, so they live here as the single referenceable source (council
#: coupling C2):
#:
#: * ``TARGET_I``   — integrated loudness, LUFS.
#: * ``TARGET_TP``  — true-peak ceiling, dBTP (headroom against inter-sample clip).
#: * ``TARGET_LRA`` — loudness range, LU.
TARGET_I = -14.0
TARGET_TP = -1.0
TARGET_LRA = 11.0

#: Mastering output sample rate. 44.1 kHz is the short-form/AAC publishing
#: baseline; a 24 kHz TTS source is upsampled to it via ``aresample`` so the
#: muxed audio track is at a standard rate.
OUTPUT_SAMPLE_RATE = 44100


class LoudnessStats(BaseModel):
    """The measured loudness of an audio source, parsed from ``loudnorm`` JSON.

    The five fields ffmpeg's first ``loudnorm`` pass prints that pass two needs
    back as ``measured_*`` / ``offset`` parameters. Strict (``extra='forbid'``)
    and explicit, matching the media-layer DTO convention (`media.schemas`); the
    raw JSON carries ~10 keys (``output_i``, ``normalization_type``, …) but only
    these feed the second pass, so the parser lifts exactly these five rather
    than splatting the whole dict (which ``extra='forbid'`` would reject).

    Fields are plain floats: the JSON values are *strings* (e.g.
    ``"input_i": "-22.50"``), which Pydantic coerces — so this is deliberately
    not strict-typed (a strict float would reject the string and the realistic
    fixture would fail to parse).
    """

    model_config = _STRICT

    input_i: float = Field(description="Measured integrated loudness, LUFS.")
    input_tp: float = Field(description="Measured true peak, dBTP.")
    input_lra: float = Field(description="Measured loudness range, LU.")
    input_thresh: float = Field(description="Measured threshold, LUFS.")
    target_offset: float = Field(description="Target offset (gain to apply), LU.")


def build_loudnorm_measure_args(audio_path: str) -> list[str]:
    """Build the ffmpeg argv for the loudness **analysis** (first) pass. Pure.

    Deterministic: given the same path it returns the same argv, every token
    explicit and assertable — it touches no I/O and mints no ids (ADR 0023).
    Runs ``loudnorm`` in measurement mode (``print_format=json``) against the
    target, decodes to a null sink (``-f null -``), and emits the measured stats
    as JSON on **stderr** for `parse_loudnorm_stats` to consume. ffmpeg exits 0
    on a successful analysis.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-i",
        audio_path,
        "-af",
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
        "-f",
        "null",
        "-",
    ]


def parse_loudnorm_stats(stderr_text: str) -> LoudnessStats:
    """Parse the ``loudnorm`` analysis JSON out of ffmpeg's stderr, or raise.

    Pure and deterministic. ffmpeg prints the measurement JSON as the **last**
    brace block on stderr, after its ``[Parsed_loudnorm_0 @ …]`` preamble lines,
    so this scans for the final ``{`` and decodes from there rather than parsing
    the whole stderr (which is not valid JSON). Avoids a silently-wrong default
    when the stats are absent or malformed: pass two depends on every field, so a
    bad analysis must fail loud (the `parse_ffprobe_duration_ms` discipline).

    Returns only the five fields pass two needs (`LoudnessStats`); the other
    ``output_*`` keys are intentionally dropped.
    """
    start = stderr_text.rfind("{")
    if start == -1:
        raise ValueError(f"loudnorm analysis JSON not found in ffmpeg stderr: {stderr_text!r}")
    try:
        # raw_decode stops at the end of the first valid object, ignoring any
        # trailing ffmpeg log lines after the closing brace.
        data, _ = json.JSONDecoder().raw_decode(stderr_text[start:])
        return LoudnessStats(
            input_i=data["input_i"],
            input_tp=data["input_tp"],
            input_lra=data["input_lra"],
            input_thresh=data["input_thresh"],
            target_offset=data["target_offset"],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        # ValidationError (not a ValueError subclass in pydantic v2) covers a
        # present-but-non-numeric measured field (e.g. ffmpeg emitting "N/A" on a
        # degenerate input) — caught here so it surfaces as the uniform error.
        raise ValueError(
            f"loudnorm analysis JSON was malformed or missing fields: {stderr_text!r}"
        ) from exc
