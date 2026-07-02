"""Real `CompositionService` adapter that shells out to the `ffmpeg` binary.

This is the concrete ffmpeg-backed renderer the protocol in
`app.media.composition.base` deferred (ADR 0019). Per CLAUDE.md §4 it is a
deterministic **tool**, never an agent: it takes the produced media assets
(narration audio + a structured caption track + ordered visuals), builds an
`ffmpeg` command, runs it, and returns the `RenderedVideo` descriptor.

Design — the load-bearing split (ADR 0023):

* **Command construction** is a pure, deterministic function,
  `build_ffmpeg_args`, that takes *already-resolved local paths* and returns the
  argv list. It creates no temp files, mints no random tokens, and touches no
  I/O — so the exact argv is unit-testable with **no ffmpeg binary present**.
* **Execution** is the async `render` method: it resolves the asset URIs to
  local paths, writes the caption track to a temp `.srt`, calls the pure builder,
  and runs the argv via `subprocess.run` (off the event loop). The two never mix.

Subprocess failures are normalized to one type, `CompositionError`: a missing
binary (`FileNotFoundError`) and any non-zero exit both surface as a clear error
carrying the human-readable command (`shlex.join`) and a tail of stderr.

The video's `duration_ms` mirrors the narration audio (the canonical
"video is as long as its narration" rule the fake documents) — we do **not**
probe the output with a second binary, keeping the result deterministic.

Audio mastering (ADR 0058): the narration is normalized to the platform-
competitive loudness target with ffmpeg's **two-pass** ``loudnorm`` before it is
muxed (a raw TTS clip lands ~8 LU too quiet). The analysis pass runs in
`render` via the same `_run` seam (off the event loop) and its measured stats
feed the second pass that `build_ffmpeg_args` emits — the pure builder stays
pure (it takes the already-measured `LoudnessStats`, runs no subprocess). The
loudness primitives (target constants, measure-pass argv, parser, stats model)
live in `app.media.composition.loudness`, the single source the QC gate
(issue #126) reuses.

Real rendering requires the binary and is covered by a
`@pytest.mark.integration` test that skips when `ffmpeg` is absent; the
argv-construction and subprocess-failure paths are fully hermetic.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.media.composition.base import RecordedRender
from app.media.composition.loudness import (
    OUTPUT_SAMPLE_RATE,
    TARGET_I,
    TARGET_LRA,
    TARGET_TP,
    LoudnessStats,
    build_loudnorm_measure_args,
    parse_loudnorm_stats,
)
from app.media.schemas import (
    DEFAULT_CAPTION_STYLE,
    CaptionStyle,
    CaptionTrack,
    RenderedVideo,
    SynthesizedSpeech,
    _gen_id,
)
from app.media.subtitles.base import format_ass, format_srt

logger = logging.getLogger(__name__)

PROVIDER_NAME = "ffmpeg"

# How many trailing characters of ffmpeg's stderr to surface in an error. A
# failed render can emit a lot; the tail carries the actual diagnostic.
_STDERR_TAIL = 2000


class CompositionError(RuntimeError):
    """Raised when the ffmpeg render cannot be performed or fails.

    Wraps both the missing-binary case (`FileNotFoundError` from the exec) and a
    non-zero ffmpeg exit, plus the pre-flight URI-resolution failures, into one
    type so callers handle render failure uniformly — symmetric with the search
    fabric's `SearchError` and ingestion's `FetchError`. Carries the
    human-readable command and a stderr tail; never a shell-injectable string
    (execution uses the argv list, never a shell).
    """


def resolve_local_path(uri: str) -> Path:
    """Resolve an asset URI to a local filesystem path for ffmpeg.

    Pure and deterministic (no filesystem access — existence is ffmpeg's
    concern). Accepts a ``file://`` URI or a bare local path; anything with a
    non-``file`` scheme (e.g. the fake's ``fake://``, or ``http://``) raises
    `CompositionError` rather than being silently handed to ffmpeg. This keeps
    the boundary explicit: the renderer never guesses at a remote fetch.
    """
    parsed = urlparse(uri)
    # A bare path ("/tmp/a.wav", "a.wav") or a Windows drive letter parses with
    # an empty scheme (or a single-letter scheme) -> treat as a local path.
    if not parsed.scheme or len(parsed.scheme) == 1:
        return Path(uri)
    if parsed.scheme == "file":
        # file:///abs/path -> /abs/path ; unquote handles percent-encoding.
        return Path(unquote(parsed.path))
    raise CompositionError(
        f"cannot resolve a local path from URI with scheme {parsed.scheme!r}: {uri!r} "
        "(the ffmpeg adapter accepts only 'file://' URIs or bare local paths)"
    )


@lru_cache(maxsize=8)
def subtitles_filter_available(ffmpeg_bin: str = "ffmpeg") -> bool:
    """Whether this ffmpeg build has the ``subtitles`` filter (requires libass).

    Burned-in captions go through the ``subtitles`` filter, which is only
    compiled in when ffmpeg is built ``--enable-libass``. Some builds (e.g. the
    current Homebrew bottle, issue #116) omit it — without this check the render
    fails cryptically ("No option name near ...") deep in the filtergraph. Cached
    (the binary's capabilities don't change within a process); failure to probe
    is treated as "unavailable" so we degrade rather than crash.
    """
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-filters"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    # `ffmpeg -filters` rows are: <flags> <name> <pads> <description>. Match the
    # NAME column (token[1]) exactly, so a filter whose *description* merely
    # mentions "subtitles" (e.g. the `ass` filter) is not a false positive.
    for raw in result.stdout.splitlines():
        parts = raw.split()
        if len(parts) >= 2 and parts[1] == b"subtitles":
            return True
    return False


def build_edit_list(*, duration_ms: int, visual_count: int) -> list[tuple[int, int]]:
    """The rendered cut structure as ordered ``(start_ms, end_ms)`` segments. Pure.

    Mirrors the *equal-slice* layout `build_ffmpeg_args` uses (each visual shown
    for an equal share of the narration), but at millisecond precision so the
    `RenderedVideo.edit_list` the QC gate's ``CUT_RHYTHM`` check ranges over tiles
    ``[0, duration_ms]`` exactly with no rounding drift: segment ``i`` runs
    ``[round(i*duration/n), round((i+1)*duration/n)]``. A single visual yields one
    full-length segment (zero cuts); N visuals yield N abutting segments. This is
    the deterministic, hermetic source of cut rhythm — **not** optical flow or
    scene detection (ADR 0060).
    """
    if visual_count <= 0:
        raise CompositionError(f"visual_count must be positive, got {visual_count}")
    if duration_ms < 0:
        raise CompositionError(f"duration_ms must be non-negative, got {duration_ms}")
    return [
        (
            round(i * duration_ms / visual_count),
            round((i + 1) * duration_ms / visual_count),
        )
        for i in range(visual_count)
    ]


def build_ffmpeg_args(
    *,
    audio_path: Path,
    visual_paths: list[Path],
    subtitles_path: Path,
    output_path: Path,
    duration_ms: int,
    width: int,
    height: int,
    loudness: LoudnessStats,
    burn_in_captions: bool = True,
) -> list[str]:
    """Build the ffmpeg argv to assemble a vertical short-form video. Pure.

    Captions: when ``burn_in_captions`` (the default, the engagement-quality path
    for short-form), they are burned into the video via the ``subtitles`` filter
    (needs an ffmpeg built with libass). When ``False``, the ``.srt`` is muxed as
    a soft ``mov_text`` track instead — a graceful fallback for ffmpeg builds
    without libass so the render still produces a valid MP4 (issue #116). The
    caller passes the result of `subtitles_filter_available`.

    Deterministic: given the same resolved paths and dimensions it returns the
    same argv, every token explicit and assertable — it creates no temp files
    and mints no random ids (those belong to the caller). The graph is minimal
    by design (ADR 0023 — the value is the construction/execution split and the
    error handling, not video artistry):

    * each visual is scaled to fit ``width``x``height`` and padded (letterboxed)
      to the exact frame so mismatched aspect ratios never stretch;
    * with several visuals they are concatenated in order (each shown for an
      equal slice of the narration) over a single ``concat`` filter; a single
      visual is simply looped under the audio;
    * the narration audio is the master clock — ``-t`` caps the output at the
      audio's length (the "video as long as narration" rule), so a looped/short
      visual is trimmed and a long one is cut.

    `duration_ms` is the narration length in milliseconds; ffmpeg's ``-t`` wants
    seconds, so it is divided (kept exact to 3 decimals).

    Audio mastering (ADR 0058): the narration is routed through ``loudnorm`` (the
    second, *linear* pass — `loudness` carries the first pass's measured stats)
    then ``aresample`` to the publishing sample rate, producing an ``[aout]``
    label in the same filtergraph; the audio map is ``-map [aout]`` and the
    output sample rate is pinned with ``-ar``. The measurement is computed by the
    caller (`render`) and supplied here, so this builder stays pure.
    """
    if not visual_paths:
        raise CompositionError("at least one visual is required to render a video")
    if duration_ms <= 0:
        raise CompositionError(f"duration_ms must be positive, got {duration_ms}")
    if width <= 0 or height <= 0:
        raise CompositionError(f"width/height must be positive, got {width}x{height}")

    # ffmpeg's -t wants seconds; keep it exact to milliseconds. Each visual gets
    # an *equal slice* of the narration so the concatenated stream sums to the
    # full length — bounding each input to the *full* duration would make concat
    # over-long and the final -t would then show only the first visual.
    n = len(visual_paths)
    duration_s = f"{duration_ms / 1000:.3f}"
    per_visual_s = f"{duration_ms / n / 1000:.3f}"  # n == 1 -> equals duration_s
    # Per-visual scale+pad: fit inside the frame preserving aspect, then pad to
    # exactly WxH (centered) with a black background.
    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )

    args: list[str] = ["ffmpeg", "-y"]
    # Visuals are short *video* clips (stock B-roll / generative), not stills, so
    # loop the whole input (`-stream_loop -1`) to fill its slice and cap the read
    # at the slice with `-t`. (`-loop 1` is an image2-demuxer option and fails on
    # a video input — "Option loop not found"; issue #119.) `-stream_loop`/`-t`
    # are input options, so they precede each `-i`.
    for path in visual_paths:
        args += ["-stream_loop", "-1", "-t", per_visual_s, "-i", str(path)]
    # The narration audio is the last input.
    args += ["-i", str(audio_path)]
    audio_index = len(visual_paths)
    # Soft-subtitle path only: add the .srt as a muxed input (no filter). Placed
    # after the audio input so the audio index is unaffected.
    subtitles_index: int | None = None
    if not burn_in_captions:
        args += ["-i", str(subtitles_path)]
        subtitles_index = audio_index + 1

    # Build the video filtergraph: scale+pad every visual; concat if >1.
    filter_parts = [f"[{i}:v]{scale_pad}[v{i}]" for i in range(len(visual_paths))]
    if len(visual_paths) == 1:
        video_label = "[v0]"
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(len(visual_paths)))
        filter_parts.append(f"{concat_inputs}concat=n={len(visual_paths)}:v=1:a=0[vcat]")
        video_label = "[vcat]"

    if burn_in_captions:
        # Burn the captions into the video stream via the subtitles filter. The
        # filename is escaped for the filtergraph mini-language (':' and '\' are
        # special inside a filter argument).
        # Escape for the filtergraph mini-language: backslash + colon are special,
        # and a single quote must use the close-escape-open pattern ('\'') because
        # ffmpeg does not backslash-escape quotes inside a single-quoted value
        # (CodeRabbit #117). Order matters: double backslashes first, then colon,
        # then quotes (whose introduced backslash must not be re-doubled).
        escaped_subs = (
            str(subtitles_path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"'\''")
        )
        filter_parts.append(f"{video_label}subtitles='{escaped_subs}'[vout]")
        video_out = "[vout]"
    else:
        # No subtitles filter (libass absent): the scaled/concatenated video is
        # the output; captions are muxed as a soft track below.
        video_out = video_label

    # Audio mastering chain (ADR 0058), in the same complex graph so its [aout]
    # label is mappable: the second (linear) loudnorm pass, fed the first pass's
    # measured stats, then aresample to the publishing sample rate. linear=true
    # makes the normalization a single fixed gain to the target rather than a
    # dynamic correction. Floats are formatted to a fixed precision so the argv
    # stays deterministic and assertable. The map below uses [aout], shared by
    # both the burn-in and soft-mux branches.
    loudnorm_chain = (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
        f":measured_I={loudness.input_i:.2f}"
        f":measured_TP={loudness.input_tp:.2f}"
        f":measured_LRA={loudness.input_lra:.2f}"
        f":measured_thresh={loudness.input_thresh:.2f}"
        f":offset={loudness.target_offset:.2f}"
        ":linear=true"
    )
    filter_parts.append(f"[{audio_index}:a]{loudnorm_chain},aresample={OUTPUT_SAMPLE_RATE}[aout]")

    args += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        video_out,
        "-map",
        "[aout]",
    ]
    if not burn_in_captions:
        # Mux the .srt as a soft mov_text subtitle track (MP4-compatible).
        args += ["-map", f"{subtitles_index}:s", "-c:s", "mov_text"]
    args += [
        # Encode settings: H.264 video + AAC audio in an MP4 — the short-form
        # publishing baseline. yuv420p keeps the output broadly playable.
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        # Pin the output sample rate to the publishing baseline (ADR 0058); the
        # aresample in the audio chain has already converted to it.
        "-ar",
        str(OUTPUT_SAMPLE_RATE),
        # Audio is the master clock: cap the whole output at the narration length
        # and stop when the shortest mapped stream ends.
        "-t",
        duration_s,
        "-shortest",
        str(output_path),
    ]
    return args


class FfmpegCompositionService:
    """A `CompositionService` that renders assets with the `ffmpeg` binary.

    Construction takes an `output_dir` (where rendered files are written); temp
    locations are otherwise managed internally. The pure `build_ffmpeg_args`
    builder and the `_run` execution seam are kept apart so the argv is testable
    without a binary and the subprocess call is a single mockable point
    (ADR 0023).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        output_dir: Path | str = Path("renders"),
        timeout: float = 600.0,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._timeout = timeout
        # Mirror the fakes' call-capture (RecordedRender) for symmetry/testability.
        self.calls: list[RecordedRender] = []

    async def render(
        self,
        *,
        audio: SynthesizedSpeech,
        captions: CaptionTrack,
        visual_uris: list[str],
        width: int = 1080,
        height: int = 1920,
        caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE,
    ) -> RenderedVideo:
        """Assemble ``audio`` + ``captions`` + ``visuals`` into one MP4.

        Resolves the asset URIs to local paths, writes the caption track to a
        transient subtitle file, builds the argv via the pure builder, and runs
        ffmpeg off the event loop. Returns a `RenderedVideo` whose ``duration_ms``
        mirrors the narration audio. Raises `CompositionError` on any failure.

        Caption file format depends on the path taken (ADR 0059): on the burn-in
        path (libass present) it writes a styled ``.ass`` via `format_ass` so the
        captions carry the brand font/colour/outline/fade; on the soft-mux
        fallback (no libass) it writes a plain ``.srt`` via `format_srt`, because
        the ``mov_text`` muxed-subtitle codec cannot carry ASS styling. The
        transient file is removed in `finally` regardless of extension.
        """
        self.calls.append(
            RecordedRender(
                audio_id=audio.id,
                caption_track_id=captions.id,
                visual_uris=list(visual_uris),
                width=width,
                height=height,
                caption_style=caption_style,
            )
        )
        if not visual_uris:
            raise CompositionError("at least one visual_uri is required to render a video")

        audio_path = resolve_local_path(audio.audio_uri)
        visual_paths = [resolve_local_path(uri) for uri in visual_uris]

        # Mint the artifact id up front so the output filename and the returned
        # DTO id are the same `vid_…` token (`_gen_id` is the same-layer helper
        # `RenderedVideo.id` defaults to — reused, not reimplemented).
        output_id = _gen_id("vid")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / f"{output_id}.mp4"

        # The caption track is structured cues; ffmpeg needs a file. Which file
        # depends on the path: probe libass *first*, then write a styled .ass
        # (burn-in) or a plain .srt (soft mov_text mux) — mov_text cannot carry
        # ASS, so the fallback stays SRT (ADR 0059). format_ass / format_srt are
        # the in-layer reuse (no reimplementation of timestamp formatting). The
        # transient subtitle file is purely an implementation detail of the
        # render, not a published artifact, so it is removed in the `finally`
        # below regardless of extension: a successful render leaves only the .mp4,
        # and a failed one cleans up its temp rather than littering `output_dir`.
        subtitles_path: Path | None = None
        try:
            # Burn captions in when this ffmpeg can (libass); otherwise degrade to
            # a soft mov_text track so the render still succeeds (issue #116).
            # Probe off the event loop — the subprocess can block up to its
            # timeout on a cold cache (CodeRabbit #117). Probe before writing so
            # the right subtitle format lands on disk.
            burn_in = await asyncio.to_thread(subtitles_filter_available)
            if burn_in:
                subtitles_path = self._output_dir / f"{output_id}.ass"
                subtitles_path.write_text(
                    format_ass(captions, style=caption_style, width=width, height=height),
                    encoding="utf-8",
                )
            else:
                logger.warning(
                    "composition: ffmpeg lacks the 'subtitles' filter (no libass) — "
                    "muxing captions as a soft mov_text track instead of burning them "
                    "in. Install an ffmpeg built with libass for styled burned-in captions."
                )
                subtitles_path = self._output_dir / f"{output_id}.srt"
                subtitles_path.write_text(format_srt(captions), encoding="utf-8")

            # First (analysis) pass: measure the narration's loudness off the
            # event loop via the same subprocess seam, so the second pass the
            # builder emits can normalize linearly to the target (ADR 0058). The
            # pure builder stays pure; the measurement is execution-side.
            loudness = await asyncio.to_thread(self._measure_loudness, audio_path)

            args = build_ffmpeg_args(
                audio_path=audio_path,
                visual_paths=visual_paths,
                subtitles_path=subtitles_path,
                output_path=output_path,
                duration_ms=audio.duration_ms,
                width=width,
                height=height,
                loudness=loudness,
                burn_in_captions=burn_in,
            )

            await asyncio.to_thread(self._run, args)
        finally:
            if subtitles_path is not None:
                subtitles_path.unlink(missing_ok=True)

        return RenderedVideo(
            id=output_id,
            # resolve() so a relative output dir (e.g. "renders") still yields a
            # valid absolute file:// URI — as_uri() rejects relative paths (#122).
            video_uri=output_path.resolve().as_uri(),
            duration_ms=audio.duration_ms,  # video is as long as its narration
            width=width,
            height=height,
            produced_via=f"composition:{self.name}",
            # Record the deterministic per-visual cut structure (the same equal
            # slices the argv lays out) so the QC gate can measure cut rhythm
            # without optical flow (ADR 0060).
            edit_list=build_edit_list(
                duration_ms=audio.duration_ms, visual_count=len(visual_paths)
            ),
        )

    def _measure_loudness(self, audio_path: Path) -> LoudnessStats:
        """Run the loudness analysis pass and parse its stats (ADR 0058).

        The execution side of the two-pass master: it builds the pure measure
        argv, runs it through the shared `_run` subprocess seam (so a missing
        binary / non-zero exit normalize to `CompositionError` like any render),
        and parses the JSON ffmpeg prints on **stderr**. A malformed/absent
        analysis surfaces as `CompositionError` rather than a silently-wrong
        normalization — the second pass depends on every measured field.
        """
        result = self._run(build_loudnorm_measure_args(str(audio_path)))
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        try:
            return parse_loudnorm_stats(stderr_text)
        except ValueError as exc:
            raise CompositionError(
                f"could not parse loudnorm analysis for {audio_path}: {exc}"
            ) from exc

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Execute the ffmpeg argv; normalize failures to `CompositionError`.

        The single subprocess seam (a mockable point). Uses the argv **list** —
        never a shell string — so there is no injection surface; `shlex.join` is
        used only to render a human-readable command in error messages.
        """
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise CompositionError(
                f"ffmpeg binary not found (is it installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CompositionError(
                f"ffmpeg timed out after {self._timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise CompositionError(
                f"ffmpeg exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return result
