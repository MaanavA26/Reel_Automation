"""Provider-neutral contract for video composition (the future FFmpeg step).

A `CompositionService` assembles the produced media assets — synthesized audio,
a caption track, and visuals — into a single rendered vertical short-form video,
returning a `RenderedVideo` descriptor. Per CLAUDE.md §3.3/§4 this is the
"composition / FFmpeg-based assembly" tool: deterministic execution, never an
agent. Async to match the repo's I/O-bound provider contract (ADR 0002/0003) —
real rendering shells out to ffmpeg (subprocess I/O) and may stream from storage.

This module ships the protocol + a hermetic `FakeCompositionService`. The
concrete ffmpeg-backed adapter (and the asset-bundle input contract it consumes)
is deferred behind the protocol — see ADR 0019. **No real ffmpeg is invoked
here.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.media.schemas import CaptionTrack, RenderedVideo, SynthesizedSpeech


@runtime_checkable
class CompositionService(Protocol):
    """A backend that renders assets into a single video.

    `visual_uris` are the ordered background/B-roll/image assets the renderer
    lays under the narration and captions. The concrete ffmpeg adapter is
    deferred (ADR 0019).
    """

    name: str

    async def render(
        self,
        *,
        audio: SynthesizedSpeech,
        captions: CaptionTrack,
        visual_uris: list[str],
        width: int = 1080,
        height: int = 1920,
    ) -> RenderedVideo: ...


@dataclass
class RecordedRender:
    """A single `render` invocation captured by the fake."""

    audio_id: str
    caption_track_id: str
    visual_uris: list[str]
    width: int
    height: int


class FakeCompositionService:
    """A hermetic `CompositionService` for offline tests (no ffmpeg, no I/O).

    Returns a deterministic `RenderedVideo` descriptor — duration mirrors the
    input audio (the canonical "video is as long as its narration" rule) and the
    dimensions echo the request — and records each call for assertions. Mirrors
    `app.services.search.fakes.FakeSearchProvider`.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[RecordedRender] = []

    async def render(
        self,
        *,
        audio: SynthesizedSpeech,
        captions: CaptionTrack,
        visual_uris: list[str],
        width: int = 1080,
        height: int = 1920,
    ) -> RenderedVideo:
        self.calls.append(
            RecordedRender(
                audio_id=audio.id,
                caption_track_id=captions.id,
                visual_uris=list(visual_uris),
                width=width,
                height=height,
            )
        )
        return RenderedVideo(
            video_uri=f"fake://composition/{len(self.calls)}.mp4",
            duration_ms=audio.duration_ms,
            width=width,
            height=height,
            produced_via=f"composition:{self.name}",
        )
