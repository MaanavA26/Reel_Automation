"""Tests for the StockVisualProvider adapter (Pexels Videos API).

Fully offline: an injected ``httpx.MockTransport`` stands in for the network, so
request building (endpoint, ``Authorization`` auth, ``per_page`` clamp,
portrait orientation) and the response → `VisualClip` mapping are verified
without a live call. The live call itself is covered by a separate
``@pytest.mark.integration`` smoke test (skipped without a key). The wire
contract asserted here mirrors the real ``GET /videos/search`` payload
(``videos[]`` with ``width``/``height``/``duration``/``user.name`` and
``video_files[]`` carrying ``link``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.media.visuals.base import VisualKind
from app.media.visuals.stock import StockVisualProvider, VisualError


def _provider(handler: Any, **kwargs: Any) -> StockVisualProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return StockVisualProvider(api_key="k", client=client, **kwargs)


def _video(
    *,
    link: str,
    width: int = 1080,
    height: int = 1920,
    duration: int = 12,
    author: str | None = "Jane Doe",
) -> dict[str, Any]:
    video: dict[str, Any] = {
        "width": width,
        "height": height,
        "duration": duration,
        "video_files": [{"link": link, "width": width, "height": height}],
    }
    if author is not None:
        video["user"] = {"name": author}
    return video


def _payload(*videos: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"videos": list(videos)})


def _search(provider: StockVisualProvider, *, query: str = "q", limit: int = 10) -> Any:
    return asyncio.run(provider.search(query=query, limit=limit))


def test_builds_request_and_maps_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url).split("?")[0]
        seen["auth"] = request.headers.get("Authorization")
        seen["params"] = dict(request.url.params)
        return _payload(
            _video(link="https://cdn/a.mp4", duration=8, author="Ann"),
            _video(link="https://cdn/b.mp4", duration=15, author="Bob"),
        )

    out = _search(_provider(handler), query="ocean")

    assert seen["url"] == "https://api.pexels.com/videos/search"
    assert seen["auth"] == "k"
    assert seen["params"] == {"query": "ocean", "per_page": "10", "orientation": "portrait"}
    assert [c.uri for c in out] == ["https://cdn/a.mp4", "https://cdn/b.mp4"]
    assert out[0].kind is VisualKind.VIDEO
    assert out[0].width == 1080
    assert out[0].height == 1920
    assert out[0].duration_ms == 8000  # seconds -> ms
    assert out[0].attribution == "Ann"
    assert out[0].produced_via == "visuals:stock"


def test_per_page_clamped_to_max_80() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return _payload()

    _search(_provider(handler), limit=500)
    assert seen["params"]["per_page"] == "80"


def test_limit_truncates_results() -> None:
    handler = lambda r: _payload(  # noqa: E731
        _video(link="https://cdn/a.mp4"),
        _video(link="https://cdn/b.mp4"),
        _video(link="https://cdn/c.mp4"),
    )
    out = _search(_provider(handler), limit=2)
    assert [c.uri for c in out] == ["https://cdn/a.mp4", "https://cdn/b.mp4"]


def test_skips_video_without_usable_file_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "videos": [
                    {"width": 1, "height": 1, "video_files": []},  # no renditions
                    {"width": 1, "height": 1, "video_files": [{"link": ""}]},  # empty link
                    _video(link="https://cdn/ok.mp4"),
                ]
            },
        )

    out = _search(_provider(handler))
    assert [c.uri for c in out] == ["https://cdn/ok.mp4"]


def test_skips_video_without_dimensions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"videos": [{"duration": 5, "video_files": [{"link": "https://cdn/x.mp4"}]}]},
        )

    assert _search(_provider(handler)) == []


def test_falls_back_to_file_dimensions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "videos": [
                    {
                        "duration": 5,
                        "video_files": [
                            {"link": "https://cdn/x.mp4", "width": 720, "height": 1280}
                        ],
                    }
                ]
            },
        )

    out = _search(_provider(handler))
    assert (out[0].width, out[0].height) == (720, 1280)


def test_missing_duration_yields_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "videos": [
                    {"width": 1080, "height": 1920, "video_files": [{"link": "https://cdn/x.mp4"}]}
                ]
            },
        )

    out = _search(_provider(handler))
    assert out[0].duration_ms is None


def test_missing_author_yields_null_attribution() -> None:
    out = _search(_provider(lambda r: _payload(_video(link="https://cdn/a.mp4", author=None))))
    assert out[0].attribution is None


def test_missing_videos_key_is_empty_not_error() -> None:
    out = _search(_provider(lambda r: httpx.Response(200, json={"page": 1})))
    assert out == []


def test_malformed_videos_shape_raises_visual_error() -> None:
    with pytest.raises(VisualError):
        _search(_provider(lambda r: httpx.Response(200, json={"videos": "nope"})))


def test_malformed_top_level_shape_raises_visual_error() -> None:
    with pytest.raises(VisualError):
        _search(_provider(lambda r: httpx.Response(200, json=["not", "a", "dict"])))


def test_http_error_surfaces() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _search(_provider(lambda r: httpx.Response(429, json={"error": "rate limited"})))


def test_missing_api_key_raises() -> None:
    with pytest.raises(VisualError):
        StockVisualProvider(api_key="")


def test_key_not_leaked_in_repr() -> None:
    provider = StockVisualProvider(api_key="super-secret-token")
    assert "super-secret-token" not in repr(provider)
