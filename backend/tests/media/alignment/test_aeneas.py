"""Tests for the aeneas `WordAligner` adapter (ADR 0062).

Hermetic by construction: the pure argv builder and sync-map parser need no
aeneas, and the adapter tests run with the single `_run` subprocess seam
mocked (the test_ffmpeg.py discipline). A real alignment is the
`@pytest.mark.integration` test at the bottom, which skips when aeneas (or
ffmpeg, used to synthesize the test audio) is absent — the adapter is a
documented-not-yet-live contract until that runs on a real machine.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import pytest

from app.media.alignment.aeneas import (
    DEFAULT_AENEAS_PYTHON,
    AeneasAligner,
    build_aeneas_task_args,
    parse_aeneas_syncmap,
)
from app.media.alignment.base import AlignmentError, WordAligner

# A realistic aeneas JSON sync map (two word fragments), as
# `aeneas.tools.execute_task` writes with os_task_file_format=json.
_SYNCMAP_JSON = json.dumps(
    {
        "fragments": [
            {
                "begin": "0.000",
                "children": [],
                "end": "0.480",
                "id": "f000001",
                "language": "eng",
                "lines": ["Hello"],
            },
            {
                "begin": "0.480",
                "children": [],
                "end": "1.120",
                "id": "f000002",
                "language": "eng",
                "lines": ["world"],
            },
        ]
    }
)


# --- build_aeneas_task_args (pure) ------------------------------------------


def test_build_args_exact_argv() -> None:
    args = build_aeneas_task_args(
        audio_path=Path("/tmp/narration.wav"),
        text_path=Path("/tmp/words.txt"),
        syncmap_path=Path("/tmp/map.json"),
    )
    assert args == [
        "python3",
        "-m",
        "aeneas.tools.execute_task",
        "/tmp/narration.wav",
        "/tmp/words.txt",
        "task_language=eng|is_text_type=plain|os_task_file_format=json",
        "/tmp/map.json",
    ]


def test_build_args_honours_language_and_python_bin() -> None:
    args = build_aeneas_task_args(
        audio_path=Path("/a.wav"),
        text_path=Path("/t.txt"),
        syncmap_path=Path("/m.json"),
        language="ita",
        python_bin="/opt/aeneas-venv/bin/python",
    )
    assert args[0] == "/opt/aeneas-venv/bin/python"
    assert args[5] == "task_language=ita|is_text_type=plain|os_task_file_format=json"


@pytest.mark.parametrize("bad", ["", "en g", "eng|foo=bar", "eng=x", "\teng"])
def test_build_args_rejects_config_breaking_language(bad: str) -> None:
    with pytest.raises(AlignmentError, match="language must be"):
        build_aeneas_task_args(
            audio_path=Path("/a.wav"),
            text_path=Path("/t.txt"),
            syncmap_path=Path("/m.json"),
            language=bad,
        )


# --- parse_aeneas_syncmap (pure) --------------------------------------------


def test_parse_syncmap_valid() -> None:
    assert parse_aeneas_syncmap(_SYNCMAP_JSON) == [(0, 480), (480, 1120)]


def test_parse_syncmap_rounds_seconds_to_ms() -> None:
    text = json.dumps({"fragments": [{"begin": "0.001", "end": "1.234"}]})
    assert parse_aeneas_syncmap(text) == [(1, 1234)]


def test_parse_syncmap_empty_fragments_is_empty() -> None:
    assert parse_aeneas_syncmap(json.dumps({"fragments": []})) == []


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({"nope": []}),  # no fragments key
        json.dumps([1, 2, 3]),  # JSON but not an object
        json.dumps({"fragments": "oops"}),  # fragments not a list
    ],
)
def test_parse_syncmap_malformed_document_raises(bad: str) -> None:
    with pytest.raises(AlignmentError, match="fragments"):
        parse_aeneas_syncmap(bad)


@pytest.mark.parametrize(
    "fragment",
    [
        {"end": "1.0"},  # missing begin
        {"begin": "abc", "end": "1.0"},  # non-numeric
        {"begin": None, "end": "1.0"},  # wrong type
        "not-a-dict",
    ],
)
def test_parse_syncmap_malformed_fragment_raises(fragment: object) -> None:
    with pytest.raises(AlignmentError, match="begin/end"):
        parse_aeneas_syncmap(json.dumps({"fragments": [fragment]}))


# --- AeneasAligner (the _run seam mocked; no aeneas needed) ------------------


def test_aligner_satisfies_protocol() -> None:
    assert isinstance(AeneasAligner(), WordAligner)


def test_align_happy_path_regroups_words_per_segment() -> None:
    """The full write → run → parse → regroup wiring, with `_run` mocked.

    The mock stands in for the aeneas subprocess: it asserts the task text was
    written one word per line and writes the sync map the real tool would.
    """
    aligner = AeneasAligner()
    seen: dict[str, str] = {}

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        # argv layout: [python, -m, aeneas.tools.execute_task, audio, text, config, syncmap]
        seen["text"] = Path(args[4]).read_text(encoding="utf-8")
        Path(args[6]).write_text(_SYNCMAP_JSON, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with patch.object(aligner, "_run", side_effect=fake_run):
        result = asyncio.run(
            aligner.align(audio_path="file:///tmp/narration.wav", segments=["Hello", "world"])
        )

    assert seen["text"] == "Hello\nworld\n"  # one word per line = word-level fragments
    assert [[w.text for w in seg] for seg in result] == [["Hello"], ["world"]]
    assert [(w.start_ms, w.end_ms) for w in result[0]] == [(0, 480)]
    assert [(w.start_ms, w.end_ms) for w in result[1]] == [(480, 1120)]


def test_align_multiword_segment_slices_flat_timings() -> None:
    aligner = AeneasAligner()

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        Path(args[6]).write_text(_SYNCMAP_JSON, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with patch.object(aligner, "_run", side_effect=fake_run):
        result = asyncio.run(aligner.align(audio_path="/tmp/n.wav", segments=["Hello world"]))

    assert len(result) == 1
    assert [(w.text, w.start_ms, w.end_ms) for w in result[0]] == [
        ("Hello", 0, 480),
        ("world", 480, 1120),
    ]


def test_align_no_words_short_circuits_without_subprocess() -> None:
    aligner = AeneasAligner()
    with patch.object(aligner, "_run") as mock_run:
        result = asyncio.run(aligner.align(audio_path="/tmp/n.wav", segments=["   ", ""]))
    assert result == [[], []]
    mock_run.assert_not_called()


def test_align_rejects_non_file_audio_uri() -> None:
    aligner = AeneasAligner()
    with pytest.raises(AlignmentError, match="scheme"):
        asyncio.run(aligner.align(audio_path="fake://tts/1.wav", segments=["hi"]))


def test_align_fragment_count_mismatch_raises() -> None:
    aligner = AeneasAligner()

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        Path(args[6]).write_text(_SYNCMAP_JSON, encoding="utf-8")  # 2 fragments
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with (
        patch.object(aligner, "_run", side_effect=fake_run),
        pytest.raises(AlignmentError, match="2 fragments for 3 words"),
    ):
        asyncio.run(aligner.align(audio_path="/tmp/n.wav", segments=["one two", "three"]))


def test_align_missing_syncmap_raises() -> None:
    aligner = AeneasAligner()

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, b"", b"")  # exits 0, writes nothing

    with (
        patch.object(aligner, "_run", side_effect=fake_run),
        pytest.raises(AlignmentError, match="wrote no sync map"),
    ):
        asyncio.run(aligner.align(audio_path="/tmp/n.wav", segments=["hi"]))


# --- the _run seam normalizes subprocess failures ----------------------------


def test_run_missing_interpreter_raises_alignment_error() -> None:
    with patch("app.media.alignment.aeneas.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(AlignmentError, match="interpreter not found"):
            AeneasAligner()._run(["python3", "-m", "aeneas.tools.execute_task"])


def test_run_nonzero_exit_raises_with_stderr_tail() -> None:
    completed = subprocess.CompletedProcess(["python3"], 1, b"", b"espeak voice missing")
    with patch("app.media.alignment.aeneas.subprocess.run", return_value=completed):
        with pytest.raises(AlignmentError, match="exited with code 1") as excinfo:
            AeneasAligner()._run(["python3"])
    assert "espeak voice missing" in str(excinfo.value)


def test_run_timeout_raises_alignment_error() -> None:
    with patch(
        "app.media.alignment.aeneas.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["python3"], timeout=1.0),
    ):
        with pytest.raises(AlignmentError, match="timed out"):
            AeneasAligner(timeout=1.0)._run(["python3"])


# --- integration: real aeneas alignment (skips without the tool) -------------


def _aeneas_importable() -> bool:
    try:
        probe = subprocess.run(
            [DEFAULT_AENEAS_PYTHON, "-c", "import aeneas"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return probe.returncode == 0


@pytest.mark.integration
def test_real_aeneas_alignment(tmp_path: Path) -> None:
    """Exercise the live aeneas contract end to end (skips when absent).

    A **contract** check, not an accuracy check: the audio is an
    ffmpeg-generated tone (not speech), so the DTW result is meaningless as
    timing — what this validates is the real CLI invocation shape, the JSON
    sync map, the one-fragment-per-word granularity, and monotonic
    non-negative timings. Timing *quality* against real narration is the
    last-mile follow-up (ADR 0062).
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg binary not on PATH (needed to synthesize test audio)")
    if not _aeneas_importable():
        pytest.skip(f"aeneas not importable via {DEFAULT_AENEAS_PYTHON!r}")

    audio = tmp_path / "narration.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=2",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )

    aligner = AeneasAligner()
    result = asyncio.run(
        aligner.align(audio_path=audio.as_uri(), segments=["hello world", "again"])
    )

    assert [len(segment) for segment in result] == [2, 1]
    flat = [span for segment in result for span in segment]
    assert [span.text for span in flat] == ["hello", "world", "again"]
    for span in flat:
        assert 0 <= span.start_ms <= span.end_ms
    for a, b in pairwise(flat):
        assert a.start_ms <= b.start_ms  # fragments come back in narration order
