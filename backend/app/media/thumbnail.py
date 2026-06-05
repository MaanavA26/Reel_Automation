"""Thumbnail renderer — extract a frame from a rendered video + overlay a title.

The visual half of the publishing-support surface (CLAUDE.md §3.4): a thumbnail
drives the click-through that drives views. Per CLAUDE.md §4 this is a
deterministic **tool**, never an agent — it shells out to `ffmpeg` exactly like
`app.media.composition.ffmpeg`, and it deliberately **mirrors that module's
load-bearing split** (ADR 0023 / ADR 0039):

* **Command construction is a pure function**, `build_thumbnail_args`, taking
  *already-resolved local paths* and primitives and returning the argv list. It
  creates no temp files, mints no random ids, and performs no I/O — so the exact
  argv is unit-testable token-by-token with **no ffmpeg binary present**.
* **Execution** is `ThumbnailRenderer.render`: it resolves the video URI to a
  local path, writes the title to a temp sidecar text file, mints the artifact
  id + output path, calls the pure builder, and runs the argv via a single
  mockable `_run` subprocess seam (off the event loop via `asyncio.to_thread`).

The title is rendered via ffmpeg's ``drawtext`` filter reading from a
``textfile=`` **sidecar** (the same pattern composition uses for the caption
``.srt``) rather than an inline ``text=`` value. This is deliberate: inline
``drawtext`` text needs a thorny escape of ``\\ : ' %`` and newlines, whereas a
sidecar means we only escape the *path* — reusing the exact filtergraph-path
escape `ffmpeg.py` already established, so the pure argv stays cleanly assertable.

``drawtext`` requires a real font file, so `fontfile` is an explicit parameter
(the renderer never guesses a system font). The integration test therefore has
**two** skip conditions — `ffmpeg` absent *or* no locatable font — beyond the
composition test's single skip.

`resolve_local_path` is reused from the composition adapter (DRY — the URI→path
rule is identical) rather than reimplemented. Failures normalize to one
`ThumbnailError`, symmetric with `CompositionError`.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.media.composition.ffmpeg import resolve_local_path
from app.media.schemas import RenderedVideo, _gen_id

PROVIDER_NAME = "thumbnail:ffmpeg"

# Official YouTube thumbnail size (16:9). Verified 2026-06 (ADR 0039): YouTube
# displays the thumbnail in 16:9 containers across search / home / channel even
# for a 9:16 Short, so 1280x720 is the safe default — but dimensions are
# *parameterized* (the vertical-vs-16:9 debate is real), so a caller can request
# 1080x1920 without a code change. Same posture as `build_ffmpeg_args`.
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

# How many trailing characters of ffmpeg's stderr to surface in an error.
_STDERR_TAIL = 2000


class ThumbnailError(RuntimeError):
    """Raised when the thumbnail render cannot be performed or fails.

    Wraps the missing-binary case (`FileNotFoundError` → "ffmpeg binary not
    found"), a non-zero ffmpeg exit, a timeout, and pre-flight validation into
    one type so callers handle failure uniformly — symmetric with the
    composition adapter's `CompositionError`. Carries the human-readable command
    (`shlex.join`) and a stderr tail; execution always uses the argv list, never
    a shell, so there is no injection surface.
    """


class Thumbnail(BaseModel):
    """A rendered thumbnail image artifact produced by a `ThumbnailRenderer`.

    A descriptor (not the image bytes), mirroring `RenderedVideo`: it points at
    where the image lives (`image_uri`) and carries the metadata a publishing
    step needs (dimensions, the source video id, the overlaid title), plus the
    required `produced_via` provenance (``"thumbnail:ffmpeg"``), symmetric with
    the other media DTOs. Defined here, not in `app/media/schemas.py`, to keep
    this module the sole writer of its own artifact type.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _gen_id("thumb"))
    image_uri: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_video_id: str
    title: str
    produced_via: str = PROVIDER_NAME


def build_thumbnail_args(
    *,
    video_path: Path,
    title_textfile: Path,
    fontfile: Path,
    output_path: Path,
    timestamp_s: float,
    width: int,
    height: int,
) -> list[str]:
    """Build the ffmpeg argv to extract one frame and overlay the title. Pure.

    Deterministic: given the same resolved paths, timestamp, and dimensions it
    returns the same argv — every token explicit and assertable. It creates no
    temp files and mints no ids (those belong to the caller). The graph:

    * ``-ss <timestamp_s>`` seeks to the frame to grab (input-side seek, fast);
    * ``-frames:v 1`` captures exactly one frame;
    * a single ``-vf`` scales+pads the frame to ``width``x``height`` (letterboxed
      so a 9:16 source is never stretched into the 16:9 default) then draws the
      title from the ``textfile=`` sidecar, centered horizontally near the
      bottom with a semi-opaque box for legibility.

    The title is read from ``title_textfile`` (a sidecar) so only the *path* is
    escaped for the filtergraph mini-language (``\\`` and ``:`` are special),
    reusing the composition adapter's path-escape — the title text itself needs
    no inline-`drawtext` escaping. ``timestamp_s`` is the seek offset in seconds;
    the caller derives it from the video duration.
    """
    if timestamp_s < 0:
        raise ThumbnailError(f"timestamp_s must be non-negative, got {timestamp_s}")
    if width <= 0 or height <= 0:
        raise ThumbnailError(f"width/height must be positive, got {width}x{height}")

    # Escape filtergraph-special chars in the paths (':' and '\'), reusing the
    # exact escape `app.media.composition.ffmpeg` applies to the subtitles path.
    escaped_textfile = str(title_textfile).replace("\\", "\\\\").replace(":", r"\:")
    escaped_fontfile = str(fontfile).replace("\\", "\\\\").replace(":", r"\:")

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    # Title near the bottom, centered, white text on a semi-opaque black box.
    # Font size scales with the frame height so it reads at any dimension.
    drawtext = (
        f"drawtext=textfile='{escaped_textfile}':fontfile='{escaped_fontfile}':"
        f"fontcolor=white:fontsize=h/16:box=1:boxcolor=black@0.5:boxborderw=20:"
        f"x=(w-text_w)/2:y=h-text_h-(h/12)"
    )

    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"{scale_pad},{drawtext}",
        str(output_path),
    ]


class ThumbnailRenderer:
    """Renders a `Thumbnail` from a `RenderedVideo` using the `ffmpeg` binary.

    Construction takes an `output_dir` (where images are written), the `fontfile`
    ``drawtext`` requires, and an optional default seek fraction. The pure
    `build_thumbnail_args` builder and the `_run` execution seam are kept apart
    so the argv is testable without a binary and the subprocess call is a single
    mockable point — the same split as `FfmpegCompositionService` (ADR 0023).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        fontfile: Path | str,
        output_dir: Path | str = Path("thumbnails"),
        frame_fraction: float = 0.5,
        timeout: float = 120.0,
    ) -> None:
        if not 0.0 <= frame_fraction <= 1.0:
            raise ThumbnailError(f"frame_fraction must be in [0, 1], got {frame_fraction}")
        self._fontfile = Path(fontfile)
        self._output_dir = Path(output_dir)
        self._frame_fraction = frame_fraction
        self._timeout = timeout

    async def render(
        self,
        *,
        video: RenderedVideo,
        title: str,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> Thumbnail:
        """Grab a frame from ``video`` and overlay ``title`` into a `Thumbnail`.

        Resolves the video URI to a local path, derives the seek timestamp from
        the video duration (``frame_fraction`` of it), writes the title to a temp
        sidecar text file, builds the argv via the pure builder, and runs ffmpeg
        off the event loop. Returns a `Thumbnail` descriptor. Raises
        `ThumbnailError` on any failure.
        """
        if not title.strip():
            raise ThumbnailError("title must be non-empty")

        video_path = resolve_local_path(video.video_uri)

        # Mint the artifact id up front so the output filename and the returned
        # DTO id are the same `thumb_…` token (reuse `_gen_id`, not reimplement).
        output_id = _gen_id("thumb")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / f"{output_id}.png"

        # The title is written to a sidecar so the filter reads it via textfile=,
        # dodging inline-drawtext escaping (see module docstring).
        title_textfile = self._output_dir / f"{output_id}.txt"
        title_textfile.write_text(title, encoding="utf-8")

        # Derive the seek timestamp from the video duration (ms → s).
        timestamp_s = (video.duration_ms / 1000.0) * self._frame_fraction

        args = build_thumbnail_args(
            video_path=video_path,
            title_textfile=title_textfile,
            fontfile=self._fontfile,
            output_path=output_path,
            timestamp_s=timestamp_s,
            width=width,
            height=height,
        )

        await asyncio.to_thread(self._run, args)

        return Thumbnail(
            id=output_id,
            image_uri=output_path.as_uri(),
            width=width,
            height=height,
            source_video_id=video.id,
            title=title,
        )

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Execute the ffmpeg argv; normalize failures to `ThumbnailError`.

        The single subprocess seam (a mockable point). Uses the argv **list** —
        never a shell string — so there is no injection surface; `shlex.join` is
        used only to render a human-readable command in error messages. Mirrors
        `FfmpegCompositionService._run`.
        """
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise ThumbnailError(
                f"ffmpeg binary not found (is it installed and on PATH?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ThumbnailError(
                f"ffmpeg timed out after {self._timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise ThumbnailError(
                f"ffmpeg exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return result
