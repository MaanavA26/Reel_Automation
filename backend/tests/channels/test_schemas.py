"""Tests for the `ChannelProfile` schema and its value types.

Fully hermetic: pure Pydantic validation, no I/O.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.channels.schemas import (
    Branding,
    ChannelProfile,
    NarrativeTone,
    Platform,
    PostingCadence,
)


def _profile(**overrides: object) -> ChannelProfile:
    base: dict[str, object] = {
        "name": "Tech in 60s",
        "niche": "applied AI explainers",
        "platforms": [Platform.YOUTUBE_SHORTS],
        "tts_voice_id": "voice_aria",
        "branding": Branding(handle="@techin60", hashtags=["#ai", "#shorts"]),
    }
    base.update(overrides)
    return ChannelProfile(**base)  # type: ignore[arg-type]


def test_minimal_profile_defaults() -> None:
    p = _profile()
    assert p.tone is NarrativeTone.EDUCATIONAL
    assert p.posting_cadence is PostingCadence.WEEKLY
    assert p.persona is None
    assert p.topic_seeds == []
    assert p.banned_topics == []


def test_id_is_prefixed_and_hex() -> None:
    p = _profile()
    assert re.fullmatch(r"chan_[0-9a-f]{16}", p.id)


def test_ids_are_unique() -> None:
    assert _profile().id != _profile().id


def test_strict_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChannelProfile(
            name="x",
            niche="y",
            platforms=[Platform.TIKTOK],
            tts_voice_id="v",
            branding=Branding(handle="@x"),
            unexpected="boom",  # type: ignore[call-arg]
        )


def test_branding_is_strict() -> None:
    with pytest.raises(ValidationError):
        Branding(handle="@x", unexpected="boom")  # type: ignore[call-arg]


def test_platforms_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        _profile(platforms=[])


def test_required_fields_enforced() -> None:
    with pytest.raises(ValidationError):
        ChannelProfile(  # type: ignore[call-arg]
            niche="y",
            platforms=[Platform.TIKTOK],
            tts_voice_id="v",
            branding=Branding(handle="@x"),
        )


def test_round_trip_dict_mode() -> None:
    p = _profile(
        topic_seeds=["transformers", "RAG"],
        tone=NarrativeTone.ENERGETIC,
        persona="a curious engineer",
        posting_cadence=PostingCadence.DAILY,
        banned_topics=["politics"],
        platforms=[Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS],
    )
    assert ChannelProfile.model_validate(p.model_dump()) == p


def test_timestamps_are_tz_aware() -> None:
    p = _profile()
    assert p.created_at.tzinfo is not None
    assert p.updated_at.tzinfo is not None


def test_enum_values_are_string_serializable() -> None:
    # StrEnum members serialize to their string value (consumed by SEO/TTS as plain strings).
    p = _profile(platforms=[Platform.TIKTOK], tone=NarrativeTone.CASUAL)
    dumped = p.model_dump(mode="json")
    assert dumped["platforms"] == ["tiktok"]
    assert dumped["tone"] == "casual"
