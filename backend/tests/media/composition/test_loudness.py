"""Tests for the shared loudness-mastering primitives (ADR 0058).

Both functions are pure: the measure-pass argv builder and the stats parser are
assertable with no ffmpeg binary present. These primitives are the single source
the render path and the QC gate (issue #126) reuse, so they are tested in their
own module rather than only through the renderer.
"""

from __future__ import annotations

import pytest

from app.media.composition.loudness import (
    OUTPUT_SAMPLE_RATE,
    TARGET_I,
    TARGET_LRA,
    TARGET_TP,
    LoudnessStats,
    build_loudnorm_measure_args,
    parse_loudnorm_stats,
)

# A realistic loudnorm analysis as ffmpeg prints it on stderr: the JSON object is
# the last brace block, preceded by a `[Parsed_loudnorm_0 @ …]` preamble line and
# it carries the ~10 keys (only five of which feed pass two).
_REAL_STDERR = """\
[Parsed_loudnorm_0 @ 0x7fda1c705000]
{
    "input_i" : "-22.50",
    "input_tp" : "-3.12",
    "input_lra" : "7.20",
    "input_thresh" : "-32.60",
    "output_i" : "-13.99",
    "output_tp" : "-1.00",
    "output_lra" : "6.90",
    "output_thresh" : "-24.10",
    "normalization_type" : "dynamic",
    "target_offset" : "-0.30"
}
"""


# --- build_loudnorm_measure_args (pure) ------------------------------------


def test_measure_args_are_analysis_mode() -> None:
    args = build_loudnorm_measure_args("/tmp/narration.wav")
    assert args[0] == "ffmpeg"
    assert "/tmp/narration.wav" in args
    af = args[args.index("-af") + 1]
    assert af.startswith(f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}")
    assert "print_format=json" in af  # analysis mode, not normalization
    # Decodes to a null sink (no output file is written).
    assert ["-f", "null", "-"] == args[-3:]


def test_measure_args_are_deterministic() -> None:
    assert build_loudnorm_measure_args("/tmp/a.wav") == build_loudnorm_measure_args("/tmp/a.wav")


# --- parse_loudnorm_stats (pure) -------------------------------------------


def test_parse_extracts_the_five_pass_two_fields() -> None:
    stats = parse_loudnorm_stats(_REAL_STDERR)
    assert isinstance(stats, LoudnessStats)
    # String JSON values are coerced to floats (the model is not strict-typed).
    assert stats.input_i == -22.5
    assert stats.input_tp == -3.12
    assert stats.input_lra == 7.2
    assert stats.input_thresh == -32.6
    assert stats.target_offset == -0.3


def test_parse_ignores_preamble_and_trailing_lines() -> None:
    # ffmpeg may emit log lines after the closing brace; the parser still finds
    # the (last) JSON object and decodes only it.
    noisy = _REAL_STDERR + "\n[out#0/null @ 0x0] video:0kB audio:1kB\nsize=N/A time=00:00:02\n"
    stats = parse_loudnorm_stats(noisy)
    assert stats.input_i == -22.5


def test_parse_raises_when_no_json() -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_loudnorm_stats("ffmpeg version 6.0\nno analysis here\n")


def test_parse_raises_on_missing_field() -> None:
    incomplete = '{ "input_i" : "-22.50", "input_tp" : "-3.12" }'
    with pytest.raises(ValueError, match="malformed or missing fields"):
        parse_loudnorm_stats(incomplete)


def test_parse_raises_on_present_but_non_numeric_field() -> None:
    # A present-but-non-numeric measured value (a degenerate ffmpeg analysis)
    # raises a pydantic ValidationError, which is NOT a ValueError subclass in
    # v2 — it must still surface as the uniform ValueError, not escape.
    non_numeric = (
        '{ "input_i" : "N/A", "input_tp" : "-3.12", "input_lra" : "7.20",'
        ' "input_thresh" : "-32.60", "target_offset" : "-0.30" }'
    )
    with pytest.raises(ValueError, match="malformed or missing fields"):
        parse_loudnorm_stats(non_numeric)


# --- target constants are referenceable (council coupling C2) --------------


def test_dod_targets_are_the_platform_competitive_values() -> None:
    # The QC gate (issue #126) measures against these same constants.
    assert (TARGET_I, TARGET_TP, TARGET_LRA) == (-14.0, -1.0, 11.0)
    assert OUTPUT_SAMPLE_RATE == 44100
