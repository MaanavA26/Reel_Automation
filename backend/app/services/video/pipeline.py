"""`VideoPipeline` — the topic → finished video orchestrator (ADR 0032).

A deterministic *service* (CLAUDE.md §4): it sequences the two completed
subsystems — the Deep Research workflow and the Media Production pipeline — into
the single end-to-end path the whole project exists to deliver. It contains no
LLM call and no judgment of its own; every reasoning step already happened inside
the injected research agents, and every media transform inside the media seams.
This module only *wires the handoff and guards it*.

The handoff guard (the load-bearing logic)
-------------------------------------------
The Deep Research run is the source of the `CreatorPacket` the media layer
consumes. A research run can terminate ``FAILED`` (a node raised) or
``COMPLETED`` (including the revision-exhausted best-effort path). Only a
``COMPLETED`` run with at least one published packet is narratable; anything else
must raise a clear `VideoPipelineError` rather than index into an empty
``packets`` list. The packet's ``narratives`` are then the media layer's concern
(`MediaPipeline` raises `MediaPipelineError` if the chosen narrative is empty).

The full retention arc, not just the outline (ADR 0063)
---------------------------------------------------------
`create_bundle` also runs the packet through `ScriptBuilder` (a pure,
deterministic tool, CLAUDE.md §4 — no LLM, no I/O) to assemble the 4-beat
HOOK → BUILD → PAYOFF → LOOP arc (ADR 0061), and passes the resulting beat texts
to `MediaPipeline.build` as its narration ``segments``. Previously the media
layer read only the narrative's own ``script_outline`` lines — the hook (which
lives solely in `CreatorPacket.hooks`) and the closing loop re-hook were
produced but never spoken or captioned. This is the integration point ADR 0061
deferred; it is a **real, visible behavior change**: renders are longer, and the
hook/loop beats are now narrated. It also makes `packet.hooks` load-bearing —
a well-formed packet has at least one hook (§5.4), but a hookless packet (that
previously rendered narrative-only) now raises `ScriptBuilderError` instead.

Dependency injection
---------------------
Both collaborator bundles are injected (`ResearchDeps`, `MediaDeps`) so the whole
pipeline runs hermetically with the repo's Fake providers and is config-gated for
a live render (`build_video_pipeline`). Visual sourcing is optional: when a
`VisualProvider` is wired the pipeline retrieves B-roll for the chosen narrative
and passes the uris through to composition; otherwise it composes over the
default background (the fake renderer's behavior — real ffmpeg requires a visual,
which is why the live media-deps builder wires a stock provider).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.core.lifecycle import AsyncClosable
from app.media.pipeline import MediaPipeline, MediaPlan
from app.media.schemas import DEFAULT_CAPTION_STYLE, CaptionStyle
from app.schemas.research_state import (
    CreatorPacket,
    JobStatus,
    NarrativeOption,
    Report,
    ResearchState,
)
from app.scripting.builder import ScriptBuilder
from app.services.composition import (
    MediaDeps,
    build_media_deps,
    build_research_deps,
)
from app.workflows.deep_research import (
    DEFAULT_MAX_SYNTHESES,
    ResearchDeps,
    run_research,
)

logger = logging.getLogger(__name__)


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8); hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous. Same scheme as ADR 0001's
    # `research_state._gen_id` — a local copy (not a cross-layer import of a
    # private symbol), the copy-not-import convention the media layer documents
    # (ADR 0019); a `VideoArtifact` is not a media DTO, so this band owns its own.
    return f"{prefix}_{secrets.token_hex(8)}"


# How many B-roll clips to request per narrative when a visual provider is wired.
# Small by design: the composition step lays them in order under the narration;
# more is not better, and the request cost stays bounded.
_DEFAULT_VISUAL_LIMIT = 3


class VideoPipelineError(RuntimeError):
    """Raised when the topic cannot be turned into a finished video.

    The failure type for the pipeline's *own* handoff guard: a research run that
    failed or produced no narratable creator packet. Downstream failures surface
    as their own originating type, not re-wrapped (so the cause stays legible):
    `ScriptBuilderError` if the packet has no hooks/narrative to script (ADR
    0063), `MediaPipelineError`/`CompositionError` for media-side failures, and
    `CompositionError` for wiring failures.
    """


class VideoArtifact(BaseModel):
    """The finished short-form video — uri + metadata + the chain's re-join keys.

    The terminal output of the whole system: where the rendered video lives
    (``video_uri``), how long it is, its dimensions, the narrative it tells, and
    the provenance ids that re-join it back through the pipeline
    (``research_state_id`` → ``creator_packet_id`` → ``media_plan_id``). Strict +
    id-prefixed like the rest of the repo's DTOs; ``produced_via`` records the
    orchestrator, symmetric with the media artifacts' own provenance.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _gen_id("reel"))
    topic: str
    research_state_id: str
    creator_packet_id: str
    media_plan_id: str
    narrative_title: str
    video_uri: str
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    produced_via: str


@dataclass(frozen=True)
class ProducedVideo:
    """The flattened `VideoArtifact` plus the rich upstream objects behind it.

    The end-to-end path produces a `VideoArtifact` (ids + uri, the public,
    serializable contract the API returns), but the **downstream** publish path —
    the closed-loop runner (ADR 0054) — needs the full objects the artifact only
    *references by id*: the `PrePublishGate` evaluates the `Report` + `CreatorPacket`
    + a candidate, and the publishing fabric uploads the `RenderedVideo` carried on
    the `MediaPlan`. Re-deriving those from ids would mean re-running research, so
    `create_bundle` returns them alongside the artifact in one immutable bundle.

    This is a *bundle of already-produced objects*, not a new DTO surface — a
    `dataclass` (like `VideoPipelineBundle`), not a Pydantic model, since every
    member is itself a validated, frozen artifact. ``report`` is resolved by
    ``packet.report_id`` (not blindly the last report) so the gate evaluates the
    exact report the packet was built from.
    """

    artifact: VideoArtifact
    research_state: ResearchState
    report: Report
    packet: CreatorPacket
    media_plan: MediaPlan


class VideoPipeline:
    """Turns a topic into a `VideoArtifact` via the research + media subsystems.

    Constructor DI mirrors `MediaPipeline`/`IngestionService`: the research and
    media collaborator bundles are required; a `MediaPipeline` is built over the
    injected `MediaDeps` (including its optional ``word_aligner``, ADR 0063, and
    optional ``narration_synthesizer``, ADR 0067).
    ``max_syntheses`` is the research revision-loop cap, forwarded to
    `run_research` (the same knob the API exposes). A `ScriptBuilder` (pure,
    stateless — CLAUDE.md §4) assembles the full retention arc per run; it needs
    no injection since it is deterministic and has no collaborators of its own.
    """

    name = "video"

    def __init__(
        self,
        research_deps: ResearchDeps,
        media_deps: MediaDeps,
        *,
        max_syntheses: int = DEFAULT_MAX_SYNTHESES,
    ) -> None:
        self._research_deps = research_deps
        self._media_deps = media_deps
        self._max_syntheses = max_syntheses
        self._media = MediaPipeline(
            media_deps.tts,
            media_deps.composition,
            voice=media_deps.voice,
            word_aligner=media_deps.word_aligner,
            narration_synthesizer=media_deps.narration_synthesizer,
        )
        self._script_builder = ScriptBuilder()

    async def create(
        self,
        topic: str,
        *,
        narrative_index: int = 0,
        caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE,
    ) -> VideoArtifact:
        """Run the full topic → finished video path and return the artifact.

        The public, API-facing surface: a thin projection of `create_bundle` down
        to the serializable `VideoArtifact`. The rich upstream objects (report,
        packet, rendered video) are dropped here — the API returns ids + uri — and
        retained only for the downstream publish path via `create_bundle`. Raises
        `VideoPipelineError` if research failed or produced no packet;
        `ScriptBuilderError` if the packet has no hooks/narrative to script (ADR
        0063); media-side failures propagate as
        `MediaPipelineError`/`CompositionError`.
        """
        bundle = await self.create_bundle(
            topic, narrative_index=narrative_index, caption_style=caption_style
        )
        return bundle.artifact

    async def create_bundle(
        self,
        topic: str,
        *,
        narrative_index: int = 0,
        caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE,
    ) -> ProducedVideo:
        """Run the full path and return the artifact **plus** its upstream objects.

        Steps: (1) run Deep Research to a terminal `ResearchState`; (2) guard that
        it completed with a narratable creator packet; (3) optionally retrieve
        B-roll for the chosen narrative; (4) build the full HOOK → BUILD → PAYOFF
        → LOOP `ShortScript` via `ScriptBuilder` (ADR 0061/0063) and build the
        `MediaPlan` from its beat texts via the media pipeline; (5) project it
        into a `VideoArtifact` and bundle it with the `Report` (resolved by
        ``packet.report_id``) + `CreatorPacket` + `MediaPlan` the downstream
        `PrePublishGate`/publisher need. `create` wraps this and returns only the
        artifact; the closed-loop runner (ADR 0054) consumes the full bundle.
        ``caption_style`` (ADR 0059) is threaded through to composition — this
        pipeline does not yet *source* a non-default style from anywhere (no
        `ChannelProfile` binding exists), it only carries a caller-supplied one
        through; the default reproduces today's rendering exactly. Same error
        contract as `create`.
        """
        logger.info("video pipeline: starting research for topic %r", topic)
        final = await run_research(
            ResearchState(topic=topic),
            deps=self._research_deps,
            max_syntheses=self._max_syntheses,
        )
        packet = self._narratable_packet(final)
        report = self._resolve_report(final, packet)

        narrative = self._select_narrative(packet, narrative_index)
        visual_uris = await self._retrieve_visuals(narrative.title)

        script = self._script_builder.build(packet, narrative_index=narrative_index)
        logger.info(
            "video pipeline: composing media for narrative %r (%d beats: %s)",
            narrative.title,
            len(script.beats),
            ", ".join(beat.role.value for beat in script.beats),
        )
        plan = await self._media.build(
            packet,
            narrative_index=narrative_index,
            visual_uris=visual_uris,
            segments=[beat.text for beat in script.beats],
            caption_style=caption_style,
        )
        artifact = self._to_artifact(topic, final, packet_id=packet.id, plan=plan)
        return ProducedVideo(
            artifact=artifact,
            research_state=final,
            report=report,
            packet=packet,
            media_plan=plan,
        )

    @staticmethod
    def _resolve_report(final: ResearchState, packet: CreatorPacket) -> Report:
        """Return the `Report` the packet was built from (by ``packet.report_id``).

        The packet carries a ``report_id`` re-join key; the safety gate must
        evaluate the *exact* report behind this packet (its caveats/citations),
        not the last report the run happened to publish. Falls back to raising a
        clear `VideoPipelineError` if the id does not resolve — a structural
        invariant violation worth failing loud rather than gating on the wrong
        report.
        """
        for report in final.publishing.reports:
            if report.id == packet.report_id:
                return report
        raise VideoPipelineError(
            f"creator packet {packet.id} references report {packet.report_id}, "
            f"which is absent from research run {final.id}'s published reports"
        )

    def _narratable_packet(self, final: ResearchState) -> CreatorPacket:
        """Return the packet to render, or raise if the run is not narratable.

        The handoff guard (see module docstring): a ``FAILED`` run, or a completed
        run that published no packet, cannot produce a video.
        """
        if final.status is not JobStatus.COMPLETED:
            raise VideoPipelineError(
                f"research run {final.id} did not complete (status={final.status.value}"
                f"{f'; error={final.error}' if final.error else ''}) — no video to render"
            )
        if not final.publishing.packets:
            raise VideoPipelineError(
                f"research run {final.id} completed but published no creator packet to render"
            )
        return final.publishing.packets[-1]

    @staticmethod
    def _select_narrative(packet: CreatorPacket, index: int) -> NarrativeOption:
        """Validate the narrative selection up front (clearer error than ffmpeg).

        `MediaPipeline.build` re-validates and would raise too, but resolving the
        narrative here lets the pipeline name the B-roll query before composition.
        """
        if not packet.narratives:
            raise VideoPipelineError(
                f"creator packet {packet.id} has no narrative options to render"
            )
        if not 0 <= index < len(packet.narratives):
            raise VideoPipelineError(
                f"narrative_index {index} out of range "
                f"(packet {packet.id} has {len(packet.narratives)} narratives)"
            )
        return packet.narratives[index]

    async def _retrieve_visuals(self, query: str) -> list[str]:
        """Retrieve B-roll uris for ``query`` when a visual provider is wired.

        Returns an empty list when no provider is configured — the fake renderer
        composes over a default background; the live ffmpeg renderer requires a
        visual, so the live media-deps builder wires a provider (ADR 0032). When a
        `VisualSink` is wired (the live path), each retrieved *remote* uri is
        bridged to a local ``file://`` uri the ffmpeg adapter can resolve — the
        stock provider mints remote ``https`` uris, which `resolve_local_path`
        would otherwise reject. The sink runs off the event loop (it is blocking
        network I/O).
        """
        provider = self._media_deps.visuals
        if provider is None:
            return []
        clips = await provider.search(query=query, limit=_DEFAULT_VISUAL_LIMIT)
        uris = [clip.uri for clip in clips]
        sink = self._media_deps.visual_sink
        if sink is None:
            return uris
        return [await asyncio.to_thread(sink, uri) for uri in uris]

    def _to_artifact(
        self,
        topic: str,
        final: ResearchState,
        *,
        packet_id: str,
        plan: MediaPlan,
    ) -> VideoArtifact:
        return VideoArtifact(
            topic=topic,
            research_state_id=final.id,
            creator_packet_id=packet_id,
            media_plan_id=plan.id,
            narrative_title=plan.narrative_title,
            video_uri=plan.video.video_uri,
            duration_ms=plan.video.duration_ms,
            width=plan.video.width,
            height=plan.video.height,
            produced_via=f"video:{self.name}",
        )


@dataclass(frozen=True)
class VideoPipelineBundle:
    """A built `VideoPipeline` plus the httpx-owning providers to close on shutdown.

    The end-to-end analogue of `ResearchBundle`/`MediaBundle` (ADR 0044): the
    pipeline aggregates the research deps (model + search + fetch clients) and the
    media deps (TTS, and stock visuals when configured), so its ``closables`` is
    the union of both bands' closables. The app lifespan drains them on shutdown.
    """

    pipeline: VideoPipeline
    closables: tuple[AsyncClosable, ...]


def build_video_pipeline(
    settings: Settings | None = None,
    *,
    max_syntheses: int = DEFAULT_MAX_SYNTHESES,
) -> VideoPipelineBundle:
    """Assemble a live `VideoPipeline` (+ its closables) from settings (ADR 0032).

    Builds both collaborator bundles via the composition root
    (`build_research_deps` + `build_media_deps`), so an unconfigured model /
    search / TTS backend surfaces as a loud `CompositionError` (mapped to 503 at
    the API seam). The live render additionally needs the ``ffmpeg`` binary and,
    for a real composition, a visual source (configured via ``stock_api_key``).
    The returned ``closables`` union both bands' httpx-owning seams so the app can
    close their clients on shutdown (ADR 0044). Tests bypass this entirely by
    constructing a `VideoPipeline` with fake-backed `ResearchDeps`/`MediaDeps`.
    """
    resolved = settings or get_settings()
    research = build_research_deps(resolved)
    media = build_media_deps(resolved)
    pipeline = VideoPipeline(
        research.deps,
        media.deps,
        max_syntheses=max_syntheses,
    )
    return VideoPipelineBundle(
        pipeline=pipeline,
        closables=(*research.closables, *media.closables),
    )
