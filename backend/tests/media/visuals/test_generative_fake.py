"""Tests for the FakeGenerativeVisualProvider + the shared seam helpers.

Fully hermetic (no network): the fake's `generate` contract, its call recording,
and the pure `_dims_for_aspect` mapping. Mirrors `test_fakes.py` for the
retrieval seam.
"""

from __future__ import annotations

import asyncio

import pytest

from app.media.visuals.base import VisualClip, VisualKind
from app.media.visuals.generative import (
    DEFAULT_ASPECT,
    FakeGenerativeVisualProvider,
    GenerativeVisualError,
    GenerativeVisualProvider,
    _dims_for_aspect,
)


def _gen(provider: FakeGenerativeVisualProvider, **kwargs: object) -> VisualClip:
    return asyncio.run(provider.generate(prompt="a calm ocean", **kwargs))  # type: ignore[arg-type]


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeGenerativeVisualProvider(), GenerativeVisualProvider)


def test_fake_generates_vertical_clip_with_provenance() -> None:
    clip = _gen(FakeGenerativeVisualProvider())
    assert clip.kind is VisualKind.VIDEO
    assert (clip.width, clip.height) == (1080, 1920)  # default 9:16
    assert clip.duration_ms == 5_000
    assert clip.produced_via == "genvideo:fake"
    assert clip.uri.startswith("fake://genvideo/")


def test_fake_records_calls_and_honors_args() -> None:
    provider = FakeGenerativeVisualProvider()
    _gen(provider, duration_ms=8_000, aspect="16:9")
    _gen(provider)
    assert [c.aspect for c in provider.calls] == ["16:9", DEFAULT_ASPECT]
    assert provider.calls[0].duration_ms == 8_000
    assert provider.calls[0].prompt == "a calm ocean"


def test_fake_scripted_clips_cycle() -> None:
    a = VisualClip(
        uri="x://1", kind=VisualKind.VIDEO, width=1, height=1, produced_via="genvideo:fake"
    )
    provider = FakeGenerativeVisualProvider(clips=[a])
    assert _gen(provider).uri == "x://1"
    assert _gen(provider).uri == "x://1"  # cycles


def test_dims_for_known_aspects() -> None:
    assert _dims_for_aspect("9:16") == (1080, 1920)
    assert _dims_for_aspect("16:9") == (1920, 1080)


def test_dims_for_unknown_aspect_raises() -> None:
    with pytest.raises(GenerativeVisualError):
        _dims_for_aspect("5:7")
