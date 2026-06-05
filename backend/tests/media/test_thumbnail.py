"""Tests for the ffmpeg-backed `ThumbnailRenderer`.

The argv-construction and subprocess-failure paths are fully hermetic (no ffmpeg
binary): `build_thumbnail_args` is pure, and `render` is exercised with the
single `_run` subprocess seam mocked. Real rendering is the
`@pytest.mark.integration` test at the bottom, which skips when ffmpeg *or* a
locatable font is absent — two skip conditions, since drawtext needs a font.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.media.composition.ffmpeg import CompositionError
from app.media.schemas import RenderedVideo
from app.media.thumbnail import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    Thumbnail,
    ThumbnailError,
    ThumbnailRenderer,
    build_thumbnail_args,
)


def _video(uri: str = "file:///tmp/out.mp4", duration_ms: int = 8000) -> RenderedVideo:
    return RenderedVideo(
        video_uri=uri,
        duration_ms=duration_ms,
        width=1080,
        height=1920,
        produced_via="composition:fake",
    )


# --- build_thumbnail_args (pure) ---------------------------------------------


def test_build_args_basic_shape() -> None:
    args = build_thumbnail_args(
        video_path=Path("/tmp/out.mp4"),
        title_textfile=Path("/tmp/t.txt"),
        fontfile=Path("/usr/share/fonts/x.ttf"),
        output_path=Path("/tmp/t.png"),
        timestamp_s=4.0,
        width=1280,
        height=720,
    )
    assert args[0] == "ffmpeg"
    assert args[-1] == "/tmp/t.png"
    assert "-frames:v" in args
    assert args[args.index("-frames:v") + 1] == "1"
    # input-side seek before -i
    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-ss") + 1] == "4.000"


def test_build_args_vf_has_scale_pad_and_drawtext() -> None:
    args = build_thumbnail_args(
        video_path=Path("/v.mp4"),
        title_textfile=Path("/t.txt"),
        fontfile=Path("/f.ttf"),
        output_path=Path("/o.png"),
        timestamp_s=1.0,
        width=1280,
        height=720,
    )
    vf = args[args.index("-vf") + 1]
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in vf
    assert "pad=1280:720" in vf
    assert "drawtext=textfile='/t.txt'" in vf
    assert "fontfile='/f.ttf'" in vf


def test_build_args_escapes_colon_in_paths() -> None:
    # A Windows-style path with a drive colon must be escaped for the filtergraph.
    args = build_thumbnail_args(
        video_path=Path("/v.mp4"),
        title_textfile=Path("C:/tmp/t.txt"),
        fontfile=Path("/f.ttf"),
        output_path=Path("/o.png"),
        timestamp_s=0.0,
        width=100,
        height=100,
    )
    vf = args[args.index("-vf") + 1]
    assert r"textfile='C\:/tmp/t.txt'" in vf


def test_build_args_rejects_negative_timestamp() -> None:
    with pytest.raises(ThumbnailError):
        build_thumbnail_args(
            video_path=Path("/v.mp4"),
            title_textfile=Path("/t.txt"),
            fontfile=Path("/f.ttf"),
            output_path=Path("/o.png"),
            timestamp_s=-1.0,
            width=100,
            height=100,
        )


def test_build_args_rejects_nonpositive_dimensions() -> None:
    with pytest.raises(ThumbnailError):
        build_thumbnail_args(
            video_path=Path("/v.mp4"),
            title_textfile=Path("/t.txt"),
            fontfile=Path("/f.ttf"),
            output_path=Path("/o.png"),
            timestamp_s=0.0,
            width=0,
            height=100,
        )


# --- render (mocked subprocess seam) -----------------------------------------


def _renderer(tmp_path: Path, **kw: object) -> ThumbnailRenderer:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"")
    return ThumbnailRenderer(fontfile=font, output_dir=tmp_path / "out", **kw)  # type: ignore[arg-type]


def test_render_returns_thumbnail_and_writes_sidecar(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    video = _video()
    with patch.object(
        renderer, "_run", return_value=subprocess.CompletedProcess([], 0, b"", b"")
    ) as run:
        thumb = asyncio.run(renderer.render(video=video, title="My Title"))
    assert isinstance(thumb, Thumbnail)
    assert thumb.produced_via == "thumbnail:ffmpeg"
    assert thumb.source_video_id == video.id
    assert thumb.title == "My Title"
    assert thumb.width == DEFAULT_WIDTH and thumb.height == DEFAULT_HEIGHT
    assert thumb.image_uri.endswith(".png")
    # the sidecar text file was written with the title
    run.assert_called_once()
    sidecars = list((tmp_path / "out").glob("*.txt"))
    assert sidecars and sidecars[0].read_text(encoding="utf-8") == "My Title"


def test_render_seek_is_fraction_of_duration(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path, frame_fraction=0.5)
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        return subprocess.CompletedProcess([], 0, b"", b"")

    with patch.object(renderer, "_run", side_effect=fake_run):
        asyncio.run(renderer.render(video=_video(duration_ms=8000), title="T"))
    args = captured["args"]
    # 8000ms * 0.5 = 4.0s
    assert args[args.index("-ss") + 1] == "4.000"


def test_render_rejects_empty_title(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    with pytest.raises(ThumbnailError):
        asyncio.run(renderer.render(video=_video(), title="   "))


def test_run_normalizes_missing_binary(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    with patch("app.media.thumbnail.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(ThumbnailError, match="ffmpeg binary not found"):
            renderer._run(["ffmpeg", "-y"])


def test_run_normalizes_nonzero_exit(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    completed = subprocess.CompletedProcess(["ffmpeg"], 1, b"", b"boom")
    with patch("app.media.thumbnail.subprocess.run", return_value=completed):
        with pytest.raises(ThumbnailError, match="exited with code 1"):
            renderer._run(["ffmpeg", "-y"])


def test_run_normalizes_timeout(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    with patch(
        "app.media.thumbnail.subprocess.run",
        side_effect=subprocess.TimeoutExpired("ffmpeg", 1.0),
    ):
        with pytest.raises(ThumbnailError, match="timed out"):
            renderer._run(["ffmpeg", "-y"])


def test_renderer_rejects_bad_frame_fraction(tmp_path: Path) -> None:
    font = tmp_path / "f.ttf"
    font.write_bytes(b"")
    with pytest.raises(ThumbnailError):
        ThumbnailRenderer(fontfile=font, frame_fraction=1.5)


def test_render_rejects_non_file_uri(tmp_path: Path) -> None:
    # resolve_local_path (reused from composition) rejects non-file schemes.
    renderer = _renderer(tmp_path)
    bad = RenderedVideo(
        video_uri="fake://x", duration_ms=1000, width=10, height=10, produced_via="composition:fake"
    )
    with pytest.raises(CompositionError):
        asyncio.run(renderer.render(video=bad, title="T"))


# --- integration (real ffmpeg + a real font) ---------------------------------


def _find_font() -> Path | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return Path(c)
    return None


@pytest.mark.integration
def test_real_render_extracts_frame_with_title(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    font = _find_font()
    if font is None:
        pytest.skip("no locatable system font for drawtext")

    # Synthesize a short test video via lavfi (same approach as the composition
    # integration test).
    video_path = tmp_path / "src.mp4"
    gen = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1080x1920:rate=30:duration=2",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(gen, capture_output=True, check=True)

    renderer = ThumbnailRenderer(fontfile=font, output_dir=tmp_path / "out")
    video = RenderedVideo(
        video_uri=video_path.as_uri(),
        duration_ms=2000,
        width=1080,
        height=1920,
        produced_via="composition:ffmpeg",
    )
    thumb = asyncio.run(renderer.render(video=video, title="Hello Thumbnail"))
    out = Path(renderer._output_dir) / f"{thumb.id}.png"
    assert out.exists() and out.stat().st_size > 0
