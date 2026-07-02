"""The Definition-of-Done rubric — the single source of QC numeric bands (ADR 0060).

`QCRubric` holds every numeric threshold the post-render QC gate evaluates a
finished `RenderedVideo` against. Per CLAUDE.md §4 this is deterministic **tool**
data, never judgment: the DoD numbers live here in exactly one place (council
spine C2) so a channel "skin" can re-tune the bands from a JSON spec *without a
code change*, and so the bands cannot drift between sites that reference them.

The loudness/true-peak/sample-rate targets are **not** redefined here — they are
re-exported from `app.media.composition.loudness` (`TARGET_I`, `TARGET_TP`,
`OUTPUT_SAMPLE_RATE`), the single source the render path also masters against
(spine C1). The rubric only adds the *tolerances* around them (e.g. ±1 LU on
integrated loudness) plus the bands that are QC-specific (length, pace, onset,
cut rhythm, caption coverage, title-safe margin).

Construction mirrors how the repo builds explainable policies (`GatePolicy`): a
frozen, fully-defaulted dataclass usable out of the box, plus `from_mapping` /
`from_json` constructors so a per-channel spec overrides only the keys it cares
about. Unknown keys are rejected (fail-loud) so a typo in a channel spec does not
silently leave a band at its default.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from app.media.composition.loudness import OUTPUT_SAMPLE_RATE, TARGET_I, TARGET_TP


@dataclass(frozen=True)
class QCRubric:
    """The Definition-of-Done bands the QC gate measures a render against.

    Every field is defaulted to the shorts DoD so a bare ``QCRubric()`` is usable;
    a per-channel JSON/dict spec overrides only the keys it tunes (see
    `from_mapping`). All bands are *inclusive* of their bounds.

    Attributes:
        min_length_s / max_length_s: total video length band (default 45-90s for
            shorts -- long enough to retain, short enough for the format).
        min_words_per_second / max_words_per_second: narration delivery pace band,
            a **post-render** check (140-170 wpm, ~2.33-2.83 wps). Distinct from
            TTS-QA's wide pre-compose plausibility band (different stage/purpose).
        max_first_caption_start_s: the hook budget — the first caption cue must
            start within this many seconds (default 2.0s).
        max_first_vo_word_s: the first spoken word must land within this many
            seconds (default 1.0s); SKIPPED today (no word-level VO timing exists).
        target_integrated_lufs / loudness_tolerance_lu: integrated loudness target
            (re-exported -14 LUFS) and the ± band (default ±1 LU).
        max_true_peak_dbtp: true-peak ceiling (re-exported -1 dBTP); a measured
            peak above this FAILs.
        min_audio_sample_rate_hz: sample-rate floor (default the publishing
            baseline `OUTPUT_SAMPLE_RATE`, 44100); below it FAILs.
        max_cut_gap_s: the longest a single visual segment may run before the next
            cut (default 3.0s) — the motion/variety floor.
        min_caption_coverage: fraction of the video duration that must be covered
            by caption cues (default 0.5); coverage is span ÷ video duration, so a
            track that ends early can fail.
        caption_title_safe_margin: the title-safe margin fraction (default 0.10);
            recorded for the deferred OCR safe-zone check (CAPTION_SAFE_ZONE).
    """

    min_length_s: float = 45.0
    max_length_s: float = 90.0
    min_words_per_second: float = 140.0 / 60.0
    max_words_per_second: float = 170.0 / 60.0
    max_first_caption_start_s: float = 2.0
    max_first_vo_word_s: float = 1.0
    target_integrated_lufs: float = TARGET_I
    loudness_tolerance_lu: float = 1.0
    max_true_peak_dbtp: float = TARGET_TP
    min_audio_sample_rate_hz: int = OUTPUT_SAMPLE_RATE
    max_cut_gap_s: float = 3.0
    min_caption_coverage: float = 0.5
    caption_title_safe_margin: float = 0.10

    @classmethod
    def from_mapping(cls, spec: Mapping[str, Any]) -> QCRubric:
        """Build a rubric from a mapping, overriding only the keys it provides.

        A per-channel spec tunes a band by name; any field it omits keeps the DoD
        default. Unknown keys raise `ValueError` (fail-loud) so a misspelled band
        is never silently ignored — a channel skin that *thinks* it loosened a
        gate but typo'd the key must fail rather than ship at the default.
        """
        known = {f.name for f in fields(cls)}
        unknown = set(spec) - known
        if unknown:
            raise ValueError(
                f"unknown QCRubric field(s): {sorted(unknown)}; valid fields: {sorted(known)}"
            )
        return cls(**dict(spec))

    @classmethod
    def from_json(cls, text: str) -> QCRubric:
        """Build a rubric from a JSON object string (a per-channel spec file).

        Thin wrapper over `from_mapping`: parses the JSON (which must be an object)
        then delegates so the same unknown-key validation applies.
        """
        data = json.loads(text)
        if not isinstance(data, Mapping):
            raise ValueError(f"QCRubric JSON must be an object, got {type(data).__name__}")
        return cls.from_mapping(data)

    def to_mapping(self) -> dict[str, Any]:
        """The rubric as a plain dict — the round-trip inverse of `from_mapping`."""
        return {f.name: getattr(self, f.name) for f in fields(self)}
