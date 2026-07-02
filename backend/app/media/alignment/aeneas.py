"""Real `WordAligner` adapter that shells out to the **aeneas** CLI (ADR 0062).

aeneas performs DTW/MFCC forced alignment against an eSpeak-synthesized
reference — no neural model, CPU-light, which is why it is the default aligner
for the 8GB-laptop constraint (issue #136). It is treated exactly like ffmpeg:
an **external subprocess contract, not a pip dependency of this repo**. aeneas
drags numpy, compiled C extensions, and a system eSpeak install; pinning it
into this package's environment would bloat and destabilize the hermetic build
for a tool the engine only ever *executes*. Whoever wants live karaoke installs
aeneas into any interpreter (its own venv is fine) and points ``python_bin`` at
it — the same posture as installing ffmpeg.

Design — the load-bearing split (mirrors ADR 0023 / the QC probe):

* **Command construction** is the pure `build_aeneas_task_args`: given already
  -resolved local paths it returns the ``python -m aeneas.tools.execute_task``
  argv, unit-testable with no aeneas present. Word-level granularity comes from
  the *input shape*, not a flag: the task text is ``is_text_type=plain`` (one
  sync fragment per line) and the adapter writes **one word per line**, so each
  fragment is one word.
* **Parsing** is the pure `parse_aeneas_syncmap`: JSON sync map text →
  ``(start_ms, end_ms)`` per fragment, fail-loud on malformed input.
* **Execution** is `AeneasAligner.align`: resolves the audio URI, writes the
  transient word list, runs the argv via the single `_run` subprocess seam (off
  the event loop), parses, and regroups the flat word timings back into
  per-segment `WordSpan` lists. Failures normalize to `AlignmentError`.

Honesty (ADR 0053 posture): this adapter is a **documented-not-yet-live
contract** — hermetic tests cover the argv builder, the parser, and the seam's
failure normalization only. Running it against real narration on a machine with
aeneas installed (and eyeballing the timing quality) is a last-mile follow-up.
One-word-per-line DTW granularity is also coarser than neural alignment; a
WhisperX adapter behind the same seam is the tracked accuracy follow-up.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from app.media.alignment.base import AlignmentError, split_words
from app.media.composition.ffmpeg import CompositionError, resolve_local_path
from app.media.schemas import WordSpan

PROVIDER_NAME = "aeneas"

# The interpreter expected to have aeneas installed. Deliberately NOT
# `sys.executable`: aeneas is an external tool contract (module docstring), so
# the default is a PATH lookup — override `python_bin` to target a dedicated
# aeneas venv.
DEFAULT_AENEAS_PYTHON = "python3"

# How many trailing characters of stderr to surface in an error (mirrors ffmpeg.py).
_STDERR_TAIL = 2000


def build_aeneas_task_args(
    *,
    audio_path: Path,
    text_path: Path,
    syncmap_path: Path,
    language: str = "eng",
    python_bin: str = DEFAULT_AENEAS_PYTHON,
) -> list[str]:
    """Build the ``aeneas.tools.execute_task`` argv for a word-level task. Pure.

    Deterministic and I/O-free (path existence is aeneas's concern). The task
    configuration string pins three things:

    * ``task_language`` — the eSpeak voice aeneas synthesizes the DTW
      reference with (aeneas language codes, e.g. ``eng``);
    * ``is_text_type=plain`` — one sync fragment per input line, which is what
      makes the one-word-per-line ``text_path`` yield **word-level** fragments;
    * ``os_task_file_format=json`` — the sync map lands as JSON for
      `parse_aeneas_syncmap`.

    ``language`` is validated against the config-string syntax (``|`` separates
    pairs, ``=`` separates key/value) so a bad code fails loudly here rather
    than corrupting the task config — the `CaptionStyle.font_name` discipline.
    """
    if not language or any(ch in "|=" or ch.isspace() for ch in language):
        raise AlignmentError(
            f"language must be a bare aeneas language code (no '|', '=', or whitespace), "
            f"got {language!r}"
        )
    config = f"task_language={language}|is_text_type=plain|os_task_file_format=json"
    return [
        python_bin,
        "-m",
        "aeneas.tools.execute_task",
        str(audio_path),
        str(text_path),
        config,
        str(syncmap_path),
    ]


def parse_aeneas_syncmap(text: str) -> list[tuple[int, int]]:
    """Parse an aeneas JSON sync map into ``(start_ms, end_ms)`` per fragment. Pure.

    The sync map is ``{"fragments": [{"begin": "0.000", "end": "0.480", ...},
    ...]}`` with begin/end as **second** strings; they convert to integer
    milliseconds via ``round(seconds * 1000)`` (aeneas emits millisecond-
    precision decimals, so rounding is exact recovery — the ms→cs *truncation*
    lock belongs to the ASS formatter, not here). Fail-loud on malformed input
    (the `parse_ffprobe_duration_ms` discipline): a missing ``fragments`` list
    or an unparseable begin/end raises `AlignmentError` rather than returning
    silently-wrong timings. An empty fragments list parses to ``[]`` — the
    caller's word-count check decides whether that is a contract violation.
    """
    try:
        fragments = json.loads(text)["fragments"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AlignmentError(f"aeneas sync map did not contain a fragments list: {text!r}") from exc
    if not isinstance(fragments, list):
        raise AlignmentError(f"aeneas sync map 'fragments' is not a list: {fragments!r}")

    timings: list[tuple[int, int]] = []
    for i, fragment in enumerate(fragments):
        try:
            start_ms = round(float(fragment["begin"]) * 1000)
            end_ms = round(float(fragment["end"]) * 1000)
        except (KeyError, TypeError, ValueError) as exc:
            raise AlignmentError(
                f"aeneas sync map fragment {i} has no parseable begin/end: {fragment!r}"
            ) from exc
        timings.append((start_ms, end_ms))
    return timings


class AeneasAligner:
    """A `WordAligner` that runs the ``aeneas`` CLI in an external interpreter.

    Construction takes the aeneas ``language`` code, the ``python_bin`` that
    has aeneas installed (an external environment — see the module docstring),
    and a subprocess ``timeout``. The pure builder/parser and the `_run`
    execution seam are kept apart so the argv and the sync-map parsing are
    testable with no aeneas present (ADR 0023 discipline).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        language: str = "eng",
        python_bin: str = DEFAULT_AENEAS_PYTHON,
        timeout: float = 300.0,
    ) -> None:
        self._language = language
        self._python_bin = python_bin
        self._timeout = timeout

    async def align(
        self,
        *,
        audio_path: str,
        segments: Sequence[str],
    ) -> list[list[WordSpan]]:
        """Force-align the narration's words and regroup them per segment.

        Writes the one-word-per-line task text to a transient temp dir, runs
        one ``execute_task`` over the **whole** narration (one subprocess per
        render, not per segment — the audio is a single file), parses the JSON
        sync map, checks the fragment count against the words fed (a mismatch
        is a broken contract, never silently mis-zipped), and slices the flat
        timings back into per-segment `WordSpan` lists. Raises
        `AlignmentError` on any failure.
        """
        words_per_segment = [split_words(segment) for segment in segments]
        flat_words = [word for words in words_per_segment for word in words]
        if not flat_words:
            return [[] for _ in words_per_segment]

        try:
            resolved_audio = resolve_local_path(audio_path)
        except CompositionError as exc:
            # Same URI contract as the renderer (file:// or bare path), but the
            # seam's own error type so the pipeline catches one thing.
            raise AlignmentError(str(exc)) from exc

        with tempfile.TemporaryDirectory(prefix="aeneas_task_") as tmp_dir:
            text_path = Path(tmp_dir) / "words.txt"
            syncmap_path = Path(tmp_dir) / "syncmap.json"
            text_path.write_text("\n".join(flat_words) + "\n", encoding="utf-8")

            args = build_aeneas_task_args(
                audio_path=resolved_audio,
                text_path=text_path,
                syncmap_path=syncmap_path,
                language=self._language,
                python_bin=self._python_bin,
            )
            await asyncio.to_thread(self._run, args)

            try:
                raw_syncmap = syncmap_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise AlignmentError(
                    f"aeneas exited 0 but wrote no sync map at {syncmap_path}"
                ) from exc

        timings = parse_aeneas_syncmap(raw_syncmap)
        if len(timings) != len(flat_words):
            raise AlignmentError(
                f"aeneas returned {len(timings)} fragments for {len(flat_words)} words "
                "(one-word-per-line contract violated)"
            )

        result: list[list[WordSpan]] = []
        index = 0
        for words in words_per_segment:
            result.append(
                [
                    WordSpan(
                        text=word,
                        start_ms=timings[index + offset][0],
                        end_ms=timings[index + offset][1],
                    )
                    for offset, word in enumerate(words)
                ]
            )
            index += len(words)
        return result

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Execute the aeneas argv; normalize failures to `AlignmentError`.

        The single subprocess seam (a mockable point), mirroring
        `FfmpegCompositionService._run`: argv **list**, never a shell string;
        `shlex.join` renders a human-readable command in errors only.
        """
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise AlignmentError(
                f"aeneas interpreter not found (is {args[0]!r} on PATH with aeneas "
                f"installed?): {shlex.join(args)}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AlignmentError(
                f"aeneas timed out after {self._timeout}s: {shlex.join(args)}"
            ) from exc

        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL:]
            raise AlignmentError(
                f"aeneas exited with code {result.returncode}: {shlex.join(args)}\n"
                f"stderr (tail):\n{stderr_tail}"
            )
        return result
