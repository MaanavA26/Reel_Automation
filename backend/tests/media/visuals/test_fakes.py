"""Tests for the hermetic FakeVisualProvider and the VisualClip DTO."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.media.visuals.base import (
    FakeVisualProvider,
    VisualClip,
    VisualKind,
    VisualProvider,
)


def _clip(uri: str, **kwargs: object) -> VisualClip:
    base: dict[str, object] = {
        "kind": VisualKind.VIDEO,
        "width": 1080,
        "height": 1920,
        "duration_ms": 5000,
        "produced_via": "visuals:fake",
    }
    base.update(kwargs)
    return VisualClip(uri=uri, **base)  # type: ignore[arg-type]


def test_satisfies_protocol() -> None:
    assert isinstance(FakeVisualProvider(), VisualProvider)


def test_returns_flat_clips_for_every_query_and_records_calls() -> None:
    clips = [_clip("https://a.mp4"), _clip("https://b.mp4")]
    provider = FakeVisualProvider(clips)

    out = asyncio.run(provider.search(query="ocean waves", limit=10))

    assert [c.uri for c in out] == ["https://a.mp4", "https://b.mp4"]
    assert provider.calls[0].query == "ocean waves"
    assert provider.calls[0].limit == 10


def test_per_query_mapping_overrides_flat_default() -> None:
    provider = FakeVisualProvider(
        [_clip("https://default.mp4")],
        by_query={"city": [_clip("https://city.mp4")]},
    )

    assert [c.uri for c in asyncio.run(provider.search(query="city"))] == ["https://city.mp4"]
    assert [c.uri for c in asyncio.run(provider.search(query="other"))] == ["https://default.mp4"]


def test_limit_truncates_results() -> None:
    provider = FakeVisualProvider([_clip("https://a.mp4"), _clip("https://b.mp4")])
    out = asyncio.run(provider.search(query="q", limit=1))
    assert [c.uri for c in out] == ["https://a.mp4"]


def test_clip_is_id_prefixed_and_strict() -> None:
    clip = _clip("https://a.mp4")
    assert clip.id.startswith("vis_")
    with pytest.raises(ValidationError):
        VisualClip(
            uri="https://a.mp4",
            kind=VisualKind.IMAGE,
            width=1080,
            height=1920,
            produced_via="visuals:fake",
            unexpected="nope",  # type: ignore[call-arg]
        )


def test_image_clip_allows_null_duration() -> None:
    still = _clip("https://a.jpg", kind=VisualKind.IMAGE, duration_ms=None)
    assert still.kind is VisualKind.IMAGE
    assert still.duration_ms is None


def test_nonpositive_dimensions_rejected() -> None:
    with pytest.raises(ValidationError):
        VisualClip(
            uri="https://a.mp4",
            kind=VisualKind.VIDEO,
            width=0,
            height=1920,
            produced_via="visuals:fake",
        )
