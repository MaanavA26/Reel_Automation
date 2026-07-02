"""The QC probe — the *only* I/O seam in the QC layer (ADR 0060).

The QC service is pure over a `QCMeasurement`: a `QCProbe` measures the **rendered
output file** (does the master actually hit target?) and hands the service a typed
measurement; the service then evaluates the rubric with no I/O of its own. This is
the same construction/execution split the composition layer uses (ADR 0023) and
mirrors the TTS probe seam (`nvidia.py`): pure argv builders + parsers, a concrete
binary-backed impl, and a hermetic fake.

What the probe measures (off the rendered MP4):

* **integrated loudness + true peak** — via the **shared** loudness primitives
  (`build_loudnorm_measure_args` + `parse_loudnorm_stats`), reused verbatim so the
  QC gate measures against the exact target the renderer mastered to (spine C1).
  `loudnorm` on a video input measures its audio track fine.
* **audio sample rate** and **soft-subtitle-stream presence** — via `ffprobe
  -show_streams`. The sample rate verifies the publishing-baseline floor. The
  soft-subtitle signal is how `CAPTIONS_BURNED_IN` is checked *from the output*:
  the soft-mux fallback (libass absent) emits a `mov_text` subtitle stream, while
  true burn-in puts captions in pixels with **no** subtitle stream. So a present
  soft subtitle stream is the signature of *not* burned-in. (This verifies the
  absence of the soft-mux signature, **not** literal pixels — pixel/OCR
  verification is deferred alongside `CAPTION_SAFE_ZONE`; see ADR 0060.)

The pure builders/parsers are assertable with no binary present; the binary-backed
`FfmpegQCProbe` is exercised only under `@pytest.mark.integration` (skips when
ffmpeg/ffprobe are absent). Hermetic tests use `FakeQCProbe` / a handed-in
`QCMeasurement`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.media.composition.loudness import (
    LoudnessStats,
    build_loudnorm_measure_args,
    parse_loudnorm_stats,
)

_STRICT = ConfigDict(extra="forbid")

# How many trailing characters of stderr to surface in an error (mirrors ffmpeg.py).
_STDERR_TAIL = 2000


class QCProbeError(RuntimeError):
    """Raised when the QC probe cannot measure the rendered file.

    Wraps a missing binary (`FileNotFoundError`), a non-zero exit, and a
    malformed/absent parse into one type so the QC service handles probe failure
    uniformly — symmetric with `CompositionError` / `NvidiaTtsError`.
    """


class QCMeasurement(BaseModel):
    """The measured properties of a rendered video file the QC service evaluates.

    A strict DTO (the probe's output, the service's input) so the evaluator is a
    pure function over it: a test can hand in a `QCMeasurement` directly, no
    binary needed. ``loudness`` reuses the shared `LoudnessStats` (its ``input_i``
    is the integrated LUFS and ``input_tp`` the true peak of the *rendered* audio).
    ``has_soft_subtitle_stream`` is the burned-in signal (see the module docstring):
    True means a soft caption track is muxed → captions are **not** burned in.
    """

    model_config = _STRICT

    loudness: LoudnessStats
    audio_sample_rate_hz: int
    has_soft_subtitle_stream: bool


@runtime_checkable
class QCProbe(Protocol):
    """A backend that measures a rendered video file into a `QCMeasurement`."""

    name: str

    def measure(self, video_path: Path) -> QCMeasurement: ...


def build_ffprobe_streams_args(video_path: Path) -> list[str]:
    """Build the ``ffprobe`` argv that lists a file's streams as JSON. Pure.

    Deterministic and I/O-free (existence is ffprobe's concern). Emits every
    stream's ``codec_type`` and audio ``sample_rate`` as JSON for
    `parse_ffprobe_streams`; the QC probe derives the audio sample rate and the
    soft-subtitle-stream presence from it.
    """
    return [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,sample_rate",
        "-of",
        "json",
        str(video_path),
    ]


def parse_ffprobe_streams(stdout: str) -> tuple[int, bool]:
    """Parse ffprobe stream JSON into ``(audio_sample_rate_hz, has_soft_subtitle)``.

    Pure and deterministic. Fail-loud (the `parse_ffprobe_duration_ms` discipline):
    a missing/unparseable audio sample rate raises rather than defaulting to a
    silently-wrong ``0``, because the sample-rate floor check depends on it. The
    soft-subtitle flag is True iff any stream's ``codec_type`` is ``"subtitle"``.
    """
    try:
        streams = json.loads(stdout)["streams"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise QCProbeError(f"ffprobe output did not contain a streams list: {stdout!r}") from exc

    has_soft_subtitle = any(s.get("codec_type") == "subtitle" for s in streams)

    sample_rates = [s.get("sample_rate") for s in streams if s.get("codec_type") == "audio"]
    if not sample_rates or sample_rates[0] is None:
        raise QCProbeError(f"ffprobe found no audio stream sample_rate: {stdout!r}")
    try:
        sample_rate = int(sample_rates[0])
    except (ValueError, TypeError) as exc:
        raise QCProbeError(f"ffprobe sample_rate is not an integer: {sample_rates[0]!r}") from exc
    return sample_rate, has_soft_subtitle


class FfmpegQCProbe:
    """A `QCProbe` that measures the rendered file with ``ffmpeg``/``ffprobe``.

    The single subprocess seam (mockable). Reuses the shared loudness measure-pass
    argv + parser (spine C1) for integrated loudness / true peak, and
    `ffprobe -show_streams` for sample rate + soft-subtitle presence. Failures
    (missing binary, non-zero exit, malformed parse) normalize to `QCProbeError`.
    """

    name = "ffmpeg"

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    def measure(self, video_path: Path) -> QCMeasurement:
        """Measure ``video_path``'s loudness, sample rate, and subtitle structure."""
        loudness = self._measure_loudness(video_path)
        sample_rate, has_soft_subtitle = self._probe_streams(video_path)
        return QCMeasurement(
            loudness=loudness,
            audio_sample_rate_hz=sample_rate,
            has_soft_subtitle_stream=has_soft_subtitle,
        )

    def _measure_loudness(self, video_path: Path) -> LoudnessStats:
        result = self._run(build_loudnorm_measure_args(str(video_path)))
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        try:
            return parse_loudnorm_stats(stderr_text)
        except ValueError as exc:
            raise QCProbeError(
                f"could not parse loudnorm analysis for {video_path}: {exc}"
            ) from exc

    def _probe_streams(self, video_path: Path) -> tuple[int, bool]:
        result = self._run(build_ffprobe_streams_args(video_path))
        return parse_ffprobe_streams(result.stdout.decode("utf-8", errors="replace"))

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Execute the argv; normalize failures to `QCProbeError` (mirrors ffmpeg.py)."""
        try:
            result = subprocess.run(args, capture_output=True, check=False, timeout=self._timeout)
        except FileNotFoundError as exc:
            raise QCProbeError(
                f"binary not found (is ffmpeg/ffprobe installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise QCProbeError(
                f"probe timed out after {self._timeout}s: {shlex.join(args)}"
            ) from exc
        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise QCProbeError(
                f"probe exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return result


class FakeQCProbe:
    """A hermetic `QCProbe` that returns a handed-in `QCMeasurement` (no I/O).

    For offline tests of the QC service over a known measurement; records each
    probed path for assertions. Mirrors `FakeCompositionService`.
    """

    name = "fake"

    def __init__(self, measurement: QCMeasurement) -> None:
        self._measurement = measurement
        self.calls: list[Path] = []

    def measure(self, video_path: Path) -> QCMeasurement:
        self.calls.append(video_path)
        return self._measurement
