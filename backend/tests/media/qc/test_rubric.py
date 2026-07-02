"""Tests for the QC rubric — the single source of DoD bands (ADR 0060).

The rubric re-exports the shared loudness/sample-rate targets and adds the
QC-specific bands. A per-channel JSON/dict spec overrides only the keys it tunes;
unknown keys fail loud.
"""

from __future__ import annotations

import pytest

from app.media.composition.loudness import OUTPUT_SAMPLE_RATE, TARGET_I, TARGET_TP
from app.media.qc.rubric import QCRubric


def test_defaults_are_the_shorts_dod() -> None:
    r = QCRubric()
    assert (r.min_length_s, r.max_length_s) == (45.0, 90.0)
    assert r.max_first_caption_start_s == 2.0
    assert r.max_first_vo_word_s == 1.0
    assert r.max_cut_gap_s == 3.0
    # 140-170 wpm expressed as words/sec.
    assert r.min_words_per_second == pytest.approx(140.0 / 60.0)
    assert r.max_words_per_second == pytest.approx(170.0 / 60.0)


def test_loudness_targets_are_reexported_not_redefined() -> None:
    # Spine C1: the rubric must reference the shared loudness constants, not a copy.
    r = QCRubric()
    assert r.target_integrated_lufs == TARGET_I
    assert r.max_true_peak_dbtp == TARGET_TP
    assert r.min_audio_sample_rate_hz == OUTPUT_SAMPLE_RATE


def test_from_mapping_overrides_only_given_keys() -> None:
    r = QCRubric.from_mapping({"max_length_s": 120.0})
    assert r.max_length_s == 120.0
    assert r.min_length_s == 45.0  # untouched default


def test_from_json_round_trips_through_to_mapping() -> None:
    import json

    original = QCRubric(min_length_s=30.0, max_cut_gap_s=2.5)
    restored = QCRubric.from_json(json.dumps(original.to_mapping()))
    assert restored == original


def test_unknown_key_fails_loud() -> None:
    with pytest.raises(ValueError, match="unknown QCRubric field"):
        QCRubric.from_mapping({"max_lenght_s": 120.0})  # typo


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        QCRubric.from_json("[1, 2, 3]")
