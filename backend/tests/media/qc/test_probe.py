"""Tests for the QC probe seam (ADR 0060).

The argv builder + stream parser are pure (no binary). The ffmpeg-backed
`FfmpegQCProbe` reuses the shared loudness primitives and is covered by an
integration test that skips without the binaries. The fake returns a handed-in
measurement.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.media.composition.loudness import LoudnessStats
from app.media.qc.probe import (
    FakeQCProbe,
    FfmpegQCProbe,
    QCMeasurement,
    QCProbeError,
    build_ffprobe_streams_args,
    parse_ffprobe_streams,
)


def _measurement(*, sample_rate: int = 44100, soft_sub: bool = False) -> QCMeasurement:
    return QCMeasurement(
        loudness=LoudnessStats(
            input_i=-14.0, input_tp=-1.5, input_lra=7.0, input_thresh=-24.0, target_offset=0.0
        ),
        audio_sample_rate_hz=sample_rate,
        has_soft_subtitle_stream=soft_sub,
    )


# --- pure argv builder + parser --------------------------------------------


def test_streams_args_request_codec_and_rate() -> None:
    args = build_ffprobe_streams_args(Path("/tmp/out.mp4"))
    assert args[0] == "ffprobe"
    assert "/tmp/out.mp4" in args
    assert "stream=codec_type,sample_rate" in args


def test_parse_streams_no_subtitle() -> None:
    stdout = (
        '{"streams": [{"codec_type": "video"},{"codec_type": "audio", "sample_rate": "44100"}]}'
    )
    rate, has_sub = parse_ffprobe_streams(stdout)
    assert rate == 44100
    assert has_sub is False


def test_parse_streams_with_soft_subtitle() -> None:
    stdout = (
        '{"streams": ['
        '{"codec_type": "video"},'
        '{"codec_type": "audio", "sample_rate": "48000"},'
        '{"codec_type": "subtitle"}]}'
    )
    rate, has_sub = parse_ffprobe_streams(stdout)
    assert rate == 48000
    assert has_sub is True


def test_parse_streams_missing_audio_rate_fails_loud() -> None:
    with pytest.raises(QCProbeError, match="no audio stream sample_rate"):
        parse_ffprobe_streams('{"streams": [{"codec_type": "video"}]}')


def test_parse_streams_malformed_fails_loud() -> None:
    with pytest.raises(QCProbeError, match="streams list"):
        parse_ffprobe_streams("not json")


# --- fake -------------------------------------------------------------------


def test_fake_probe_returns_handed_in_measurement() -> None:
    m = _measurement(soft_sub=True)
    probe = FakeQCProbe(m)
    assert probe.measure(Path("/tmp/out.mp4")) is m
    assert probe.calls == [Path("/tmp/out.mp4")]


# --- integration (skips without binaries) ----------------------------------


@pytest.mark.integration
def test_real_probe_measures_a_rendered_tone(tmp_path: Path) -> None:
    """Measure a real MP4's loudness, sample rate, and subtitle structure.

    Skips when ffmpeg/ffprobe are absent. Renders a 1s teal video with a 440 Hz
    tone at 44.1 kHz (no subtitle track) and asserts the probe reads it back.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")

    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=teal:s=1080x1920:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )

    measurement = FfmpegQCProbe().measure(out)
    assert measurement.audio_sample_rate_hz == 44100
    assert measurement.has_soft_subtitle_stream is False
    assert measurement.loudness.input_i < 0  # a real measured LUFS
