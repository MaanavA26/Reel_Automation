"""Tests for the ffmpeg-backed CompositionService.

The argv-construction and subprocess-failure paths are fully hermetic (no
ffmpeg binary): `build_ffmpeg_args` is pure, and `render` is exercised with the
single `_run` subprocess seam mocked. Real rendering is the `@pytest.mark.integration`
test at the bottom, which skips when ffmpeg is absent.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.media.composition.base import CompositionService
from app.media.composition.ffmpeg import (
    CompositionError,
    FfmpegCompositionService,
    build_ffmpeg_args,
    resolve_local_path,
)
from app.media.composition.loudness import LoudnessStats
from app.media.schemas import Caption, CaptionTrack, RenderedVideo, SynthesizedSpeech


def _loudness() -> LoudnessStats:
    # A realistic too-quiet measurement (the issue's ~-22 LUFS source).
    return LoudnessStats(
        input_i=-22.5, input_tp=-3.1, input_lra=7.2, input_thresh=-32.6, target_offset=-0.3
    )


def _audio(uri: str = "file:///tmp/narration.wav", duration_ms: int = 4200) -> SynthesizedSpeech:
    return SynthesizedSpeech(
        audio_uri=uri, duration_ms=duration_ms, voice="narrator", produced_via="tts:fake"
    )


def _captions() -> CaptionTrack:
    return CaptionTrack(
        cues=[
            Caption(start_ms=0, end_ms=2000, text="Hello"),
            Caption(start_ms=2000, end_ms=4200, text="World"),
        ],
        produced_via="subtitles:deterministic",
    )


# --- resolve_local_path (pure) ---------------------------------------------


def test_resolve_bare_path() -> None:
    assert resolve_local_path("/tmp/a.wav") == Path("/tmp/a.wav")


def test_resolve_relative_bare_path() -> None:
    assert resolve_local_path("assets/bg.png") == Path("assets/bg.png")


def test_resolve_file_uri_unquotes() -> None:
    assert resolve_local_path("file:///tmp/my%20clip.mp4") == Path("/tmp/my clip.mp4")


def test_resolve_rejects_non_file_scheme() -> None:
    with pytest.raises(CompositionError, match="scheme"):
        resolve_local_path("fake://composition/bg.png")
    with pytest.raises(CompositionError, match="scheme"):
        resolve_local_path("https://example.com/bg.png")


# --- build_ffmpeg_args (pure, deterministic) -------------------------------


def _build_single() -> list[str]:
    return build_ffmpeg_args(
        audio_path=Path("/tmp/a.wav"),
        visual_paths=[Path("/tmp/bg.png")],
        subtitles_path=Path("/tmp/out.srt"),
        output_path=Path("/tmp/out.mp4"),
        duration_ms=4200,
        width=1080,
        height=1920,
        loudness=_loudness(),
    )


def test_build_is_deterministic() -> None:
    assert _build_single() == _build_single()


def test_build_single_visual_argv_tokens() -> None:
    args = _build_single()
    assert args[0] == "ffmpeg"
    assert "-y" in args
    # Audio is the last input; the video clip is stream-looped + bounded to its slot.
    assert args.count("-i") == 2
    assert "/tmp/a.wav" in args
    assert "/tmp/bg.png" in args
    sl = args.index("-stream_loop")
    assert ["-stream_loop", "-1"] == args[sl : sl + 2]
    # Duration in seconds appears for -t (4200ms -> 4.200s).
    assert "4.200" in args
    # Output path is last.
    assert args[-1] == "/tmp/out.mp4"
    # Single visual -> no concat in the filtergraph.
    fc = args[args.index("-filter_complex") + 1]
    assert "concat" not in fc
    assert "scale=1080:1920" in fc
    assert "pad=1080:1920" in fc
    assert "subtitles='/tmp/out.srt'" in fc
    assert "[vout]" in fc
    # Master-clock plumbing.
    assert "-shortest" in args
    assert ["-map", "[vout]"] == args[args.index("[vout]") - 1 : args.index("[vout]") + 1]
    assert "libx264" in args
    assert "aac" in args
    assert "yuv420p" in args
    # Audio mastering (ADR 0058): the second loudnorm pass + aresample define
    # [aout], the audio is mapped from it, and the sample rate is pinned.
    assert "loudnorm=I=-14.0:TP=-1.0:LRA=11.0" in fc
    assert "measured_I=-22.50" in fc  # the first pass's measured stats fed back
    assert "linear=true" in fc
    assert "aresample=44100" in fc
    assert "[aout]" in fc
    # The audio is mapped from the [aout] filtergraph label (the token "[aout]"
    # in the argv list — not the filtergraph string — is the -map target).
    aout_map = args.index("[aout]")
    assert args[aout_map - 1] == "-map"
    assert ["-ar", "44100"] == args[args.index("-ar") : args.index("-ar") + 2]


def test_build_multi_visual_concats_in_order() -> None:
    args = build_ffmpeg_args(
        audio_path=Path("/tmp/a.wav"),
        visual_paths=[Path("/tmp/1.png"), Path("/tmp/2.png"), Path("/tmp/3.png")],
        subtitles_path=Path("/tmp/out.srt"),
        output_path=Path("/tmp/out.mp4"),
        duration_ms=9000,
        width=720,
        height=1280,
        loudness=_loudness(),
    )
    assert args.count("-i") == 4  # 3 visuals + 1 audio
    fc = args[args.index("-filter_complex") + 1]
    assert "[v0]" in fc and "[v1]" in fc and "[v2]" in fc
    assert "concat=n=3:v=1:a=0[vcat]" in fc
    # Concat consumes the labels in input order.
    assert "[v0][v1][v2]concat" in fc
    assert "scale=720:1280" in fc
    # Audio is input index 3 (after the 3 visuals): it is mastered in the
    # filtergraph (from [3:a] into [aout], ADR 0058) and mapped from that label.
    assert "[3:a]loudnorm" in fc
    aout_map = args.index("[aout]")
    assert args[aout_map - 1] == "-map"
    # Each visual gets an equal slice (9000ms / 3 = 3.000s per input) so the
    # concatenated stream sums to the narration length; the final -t caps the
    # whole output at 9.000s. (This distinguishes the correct command from the
    # bug where every input is bounded to the full duration and concat overruns.)
    assert args.count("3.000") == 3  # one per-visual -t
    assert args[args.index("-shortest") - 1] == "9.000"  # final master-clock cap


def test_build_subtitles_path_is_escaped() -> None:
    args = build_ffmpeg_args(
        audio_path=Path("/tmp/a.wav"),
        visual_paths=[Path("/tmp/bg.png")],
        subtitles_path=Path("/tmp/sub:dir/out.srt"),
        output_path=Path("/tmp/out.mp4"),
        duration_ms=1000,
        width=1080,
        height=1920,
        loudness=_loudness(),
    )
    fc = args[args.index("-filter_complex") + 1]
    # The ':' inside the subtitles filename is escaped for the filtergraph.
    assert r"subtitles='/tmp/sub\:dir/out.srt'" in fc


def test_build_subtitles_single_quote_is_escaped() -> None:
    # A single quote in the path must use the close-escape-open pattern '\'' so it
    # doesn't break the single-quoted filter value (CodeRabbit #117, Critical).
    args = build_ffmpeg_args(
        audio_path=Path("/tmp/a.wav"),
        visual_paths=[Path("/tmp/bg.png")],
        subtitles_path=Path("/tmp/o'x/out.srt"),
        output_path=Path("/tmp/out.mp4"),
        duration_ms=1000,
        width=1080,
        height=1920,
        loudness=_loudness(),
    )
    fc = args[args.index("-filter_complex") + 1]
    assert r"o'\''x" in fc


def test_build_soft_subtitles_mux_when_libass_absent() -> None:
    # burn_in_captions=False (no libass): no subtitles filter; the .srt is a muxed
    # input mapped as a soft mov_text track instead (issue #116).
    args = build_ffmpeg_args(
        audio_path=Path("/tmp/a.wav"),
        visual_paths=[Path("/tmp/bg.png")],
        subtitles_path=Path("/tmp/out.srt"),
        output_path=Path("/tmp/out.mp4"),
        duration_ms=4200,
        width=1080,
        height=1920,
        loudness=_loudness(),
        burn_in_captions=False,
    )
    fc = args[args.index("-filter_complex") + 1]
    assert "subtitles=" not in fc  # NOT burned in
    assert "[v0]" in fc  # single-visual scale label is the video output
    # Three inputs now: visual, audio, and the .srt; the srt is a soft mov_text track.
    assert args.count("-i") == 3
    assert "/tmp/out.srt" in args
    assert ["-c:s", "mov_text"] == args[args.index("-c:s") : args.index("-c:s") + 2]
    assert "2:s" in args  # subtitle stream mapped from the srt input (index 2)
    assert ["-map", "[v0]"] == args[args.index("[v0]") - 1 : args.index("[v0]") + 1]


def test_subtitles_filter_available_parses_name_column() -> None:
    # Match the NAME column exactly: a row whose *description* mentions
    # "subtitles" (the `ass` filter) must NOT be a false positive (CodeRabbit #117).
    from app.media.composition.ffmpeg import subtitles_filter_available

    present = b" T.. subtitles        V->V       Render text subtitles onto input video.\n"
    # `ass` row mentions "subtitles" only in its description; `scale` is unrelated.
    absent = (
        b" T.. ass              V->V       Render ASS subtitles onto input video.\n"
        b" ..C scale            V->V       Scale the input video.\n"
    )
    with patch("app.media.composition.ffmpeg.subprocess.run") as m:
        subtitles_filter_available.cache_clear()
        m.return_value = subprocess.CompletedProcess([], 0, present, b"")
        assert subtitles_filter_available("ffmpeg-a") is True

        subtitles_filter_available.cache_clear()
        m.return_value = subprocess.CompletedProcess([], 0, absent, b"")
        assert subtitles_filter_available("ffmpeg-b") is False  # desc-mention is not a match

        subtitles_filter_available.cache_clear()
        m.return_value = subprocess.CompletedProcess([], 1, present, b"")
        assert subtitles_filter_available("ffmpeg-c") is False  # nonzero exit -> unavailable

    # Missing binary -> False, never raises.
    subtitles_filter_available.cache_clear()
    assert subtitles_filter_available("definitely-not-a-real-ffmpeg-binary") is False


def test_build_rejects_no_visuals() -> None:
    with pytest.raises(CompositionError, match="at least one visual"):
        build_ffmpeg_args(
            audio_path=Path("/tmp/a.wav"),
            visual_paths=[],
            subtitles_path=Path("/tmp/out.srt"),
            output_path=Path("/tmp/out.mp4"),
            duration_ms=1000,
            width=1080,
            height=1920,
            loudness=_loudness(),
        )


def test_build_rejects_nonpositive_duration() -> None:
    with pytest.raises(CompositionError, match="duration_ms must be positive"):
        build_ffmpeg_args(
            audio_path=Path("/tmp/a.wav"),
            visual_paths=[Path("/tmp/bg.png")],
            subtitles_path=Path("/tmp/out.srt"),
            output_path=Path("/tmp/out.mp4"),
            duration_ms=0,
            width=1080,
            height=1920,
            loudness=_loudness(),
        )


# --- render (subprocess seam mocked) ---------------------------------------


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegCompositionService(), CompositionService)


def test_render_returns_descriptor_and_records_call(tmp_path: Path) -> None:
    service = FfmpegCompositionService(output_dir=tmp_path)
    audio = _audio(uri="/tmp/a.wav")
    # The loudness analysis pass is its own subprocess seam; patch it so this test
    # exercises only the render call (ADR 0058 — the measure pass is tested apart).
    with (
        patch.object(service, "_measure_loudness", return_value=_loudness()),
        patch.object(
            service, "_run", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ) as mock_run,
    ):
        video = asyncio.run(
            service.render(audio=audio, captions=_captions(), visual_uris=["/tmp/bg.png"])
        )
    mock_run.assert_called_once()
    # The argv passed to _run starts with the binary and ends at the output.
    argv = mock_run.call_args.args[0]
    assert argv[0] == "ffmpeg"
    assert argv[-1].endswith(".mp4")
    assert isinstance(video, RenderedVideo)
    assert video.duration_ms == audio.duration_ms  # video matches narration
    assert (video.width, video.height) == (1080, 1920)  # vertical default
    assert video.produced_via == "composition:ffmpeg"
    assert video.video_uri.startswith("file://")
    # Call captured for assertions (mirrors the fake).
    assert service.calls[0].audio_id == audio.id
    assert service.calls[0].visual_uris == ["/tmp/bg.png"]


def test_render_relative_output_dir_yields_absolute_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative output dir (the default "renders") must still produce a valid
    # absolute file:// URI — as_uri() rejects relative paths (#122).
    monkeypatch.chdir(tmp_path)
    service = FfmpegCompositionService(output_dir="renders")  # relative!
    with (
        patch.object(service, "_measure_loudness", return_value=_loudness()),
        patch.object(service, "_run", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
    ):
        video = asyncio.run(
            service.render(
                audio=_audio(uri="/tmp/a.wav"),
                captions=_captions(),
                visual_uris=["/tmp/bg.png"],
            )
        )
    assert video.video_uri.startswith("file:///")  # absolute, not a relative URI
    assert video.video_uri.endswith(".mp4")


def test_render_feeds_srt_to_ffmpeg_then_cleans_it_up(tmp_path: Path) -> None:
    """The captions reach ffmpeg via a transient .srt that the render removes.

    The .srt is an implementation detail, not a published artifact: it must exist
    *during* the ffmpeg call (so `format_srt` output is what gets burned in) and be
    gone once a successful render returns — only the .mp4 is the contract.
    """
    service = FfmpegCompositionService(output_dir=tmp_path)
    captured: dict[str, str] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        # The subtitles path is the last token of the subtitles='...' filter arg.
        srt_path = next(p for p in tmp_path.glob("*.srt"))
        captured["contents"] = srt_path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess([], 0, b"", b"")

    with (
        patch.object(service, "_measure_loudness", return_value=_loudness()),
        patch.object(service, "_run", side_effect=fake_run),
    ):
        asyncio.run(
            service.render(
                audio=_audio(uri="/tmp/a.wav"), captions=_captions(), visual_uris=["/tmp/bg.png"]
            )
        )

    # During the call the .srt carried the formatted cues...
    assert "00:00:00,000 --> 00:00:02,000" in captured["contents"]
    assert "Hello" in captured["contents"] and "World" in captured["contents"]
    # ...and a successful render leaves only the .mp4 behind, no .srt litter.
    assert list(tmp_path.glob("*.srt")) == []


def test_render_cleans_up_srt_on_failure(tmp_path: Path) -> None:
    """A failed render removes its transient .srt rather than littering output_dir."""
    service = FfmpegCompositionService(output_dir=tmp_path)
    with patch.object(service, "_run", side_effect=CompositionError("ffmpeg exited with code 1")):
        with pytest.raises(CompositionError):
            asyncio.run(
                service.render(
                    audio=_audio(uri="/tmp/a.wav"),
                    captions=_captions(),
                    visual_uris=["/tmp/bg.png"],
                )
            )
    assert list(tmp_path.glob("*.srt")) == []


def test_render_echoes_requested_dimensions(tmp_path: Path) -> None:
    service = FfmpegCompositionService(output_dir=tmp_path)
    with (
        patch.object(service, "_measure_loudness", return_value=_loudness()),
        patch.object(service, "_run", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
    ):
        video = asyncio.run(
            service.render(
                audio=_audio(uri="/tmp/a.wav"),
                captions=_captions(),
                visual_uris=["/tmp/bg.png"],
                width=720,
                height=1280,
            )
        )
    assert (video.width, video.height) == (720, 1280)


def test_render_rejects_empty_visuals(tmp_path: Path) -> None:
    service = FfmpegCompositionService(output_dir=tmp_path)
    with pytest.raises(CompositionError, match="at least one visual_uri"):
        asyncio.run(service.render(audio=_audio(), captions=_captions(), visual_uris=[]))


def test_render_rejects_non_file_uri(tmp_path: Path) -> None:
    service = FfmpegCompositionService(output_dir=tmp_path)
    with pytest.raises(CompositionError, match="scheme"):
        asyncio.run(
            service.render(
                audio=_audio(uri="fake://a.wav"),
                captions=_captions(),
                visual_uris=["/tmp/bg.png"],
            )
        )


# --- _run error normalization (the execution seam) -------------------------


def test_run_wraps_missing_binary() -> None:
    service = FfmpegCompositionService()
    with patch("app.media.composition.ffmpeg.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(CompositionError, match="ffmpeg binary not found"):
            service._run(["ffmpeg", "-y"])


def test_run_wraps_nonzero_exit_with_stderr_tail() -> None:
    service = FfmpegCompositionService()
    failed = subprocess.CompletedProcess(["ffmpeg"], 1, b"", b"Invalid argument boom")
    with patch("app.media.composition.ffmpeg.subprocess.run", return_value=failed):
        with pytest.raises(CompositionError, match="exited with code 1") as exc:
            service._run(["ffmpeg", "-y", "out.mp4"])
    assert "Invalid argument boom" in str(exc.value)


def test_run_wraps_timeout() -> None:
    service = FfmpegCompositionService()
    with patch(
        "app.media.composition.ffmpeg.subprocess.run",
        side_effect=subprocess.TimeoutExpired("ffmpeg", 600.0),
    ):
        with pytest.raises(CompositionError, match="timed out"):
            service._run(["ffmpeg", "-y"])


def test_run_returns_completed_process_on_success() -> None:
    service = FfmpegCompositionService()
    ok = subprocess.CompletedProcess(["ffmpeg"], 0, b"", b"")
    with patch("app.media.composition.ffmpeg.subprocess.run", return_value=ok):
        assert service._run(["ffmpeg", "-y"]) is ok


# --- _measure_loudness (the analysis-pass execution seam) ------------------


_LOUDNORM_STDERR = (
    b"[Parsed_loudnorm_0 @ 0x600000] \n"
    b"{\n"
    b'    "input_i" : "-22.50",\n'
    b'    "input_tp" : "-3.12",\n'
    b'    "input_lra" : "7.20",\n'
    b'    "input_thresh" : "-32.60",\n'
    b'    "output_i" : "-13.99",\n'
    b'    "output_tp" : "-1.00",\n'
    b'    "output_lra" : "6.90",\n'
    b'    "output_thresh" : "-24.10",\n'
    b'    "normalization_type" : "dynamic",\n'
    b'    "target_offset" : "-0.30"\n'
    b"}\n"
)


def test_measure_loudness_runs_pass_and_parses_stats() -> None:
    service = FfmpegCompositionService()
    completed = subprocess.CompletedProcess(["ffmpeg"], 0, b"", _LOUDNORM_STDERR)
    with patch.object(service, "_run", return_value=completed) as mock_run:
        stats = service._measure_loudness(Path("/tmp/a.wav"))
    # The measure-pass argv (analysis mode) reached the subprocess seam.
    argv = mock_run.call_args.args[0]
    assert "print_format=json" in " ".join(argv)
    assert "/tmp/a.wav" in argv
    # The parsed stats are the source's measured loudness (the too-quiet -22 LUFS).
    assert stats.input_i == -22.5
    assert stats.target_offset == -0.3


def test_measure_loudness_wraps_unparseable_analysis() -> None:
    service = FfmpegCompositionService()
    completed = subprocess.CompletedProcess(["ffmpeg"], 0, b"", b"no json here")
    with patch.object(service, "_run", return_value=completed):
        with pytest.raises(CompositionError, match="could not parse loudnorm analysis"):
            service._measure_loudness(Path("/tmp/a.wav"))


# --- integration: real ffmpeg render (skips without the binary) ------------


@pytest.mark.integration
def test_real_ffmpeg_render(tmp_path: Path) -> None:
    """Render a real MP4 with the ffmpeg binary using lavfi-generated inputs.

    Skips when ffmpeg is not on PATH. Inputs are synthesized by ffmpeg itself
    (a color source + a real tone), so no binary fixtures live in the repo. The
    audio is a 440 Hz **sine**, not silence: the two-pass loudnorm master
    (ADR 0058) measures the source, and digital silence yields a degenerate
    ``-inf``/``-70`` analysis that makes pass two misbehave.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg binary not on PATH")

    # Generate a 2s 1080x1920 solid-color still and 2s of a 440 Hz tone.
    image = tmp_path / "bg.png"
    audio = tmp_path / "a.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=teal:s=1080x1920:d=1",
            "-frames:v",
            "1",
            str(image),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000:duration=2",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )

    service = FfmpegCompositionService(output_dir=tmp_path)
    video = asyncio.run(
        service.render(
            audio=_audio(uri=audio.as_uri(), duration_ms=2000),
            captions=_captions(),
            visual_uris=[image.as_uri()],
        )
    )

    out = Path(resolve_local_path(video.video_uri))
    assert out.exists() and out.stat().st_size > 0
    assert video.duration_ms == 2000
    assert (video.width, video.height) == (1080, 1920)

    # The mastered audio track is at the publishing sample rate (ADR 0058).
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "default=nw=1:nk=1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "44100"
