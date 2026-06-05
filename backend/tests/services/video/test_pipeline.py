"""Hermetic end-to-end tests for the `VideoPipeline` linchpin (ADR 0032).

Fully hermetic: the Deep Research workflow runs over `FakeProvider`-backed agents
+ fetch/search fakes, and the media layer over `FakeTTSProvider` +
`FakeCompositionService` (the real, pure `DeterministicSubtitleService`). No
network, no LLM, no ffmpeg, no PyPI — this proves the whole topic → finished
video-artifact path with zero external dependencies.

The crucial seam this exercises that the per-subsystem tests do not: the
research-side fake **must** produce a `CreatorPacket` carrying a narratable
`NarrativeOption`, or the media layer has nothing to render. So the fake
strategist scripts a narrative with a multi-line script outline, and the test
asserts the produced video's metadata is coherent with that narrative.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.creator_packet import (
    CreatorPacketAgent,
    _NarrativeDraft,
    _PacketOutput,
)
from app.agents.cross_verification import (
    CrossVerificationAgent,
    _VerdictDraft,
    _VerificationOutput,
)
from app.agents.editorial_critic import EditorialCriticAgent, _CritiqueOutput
from app.agents.evidence_extraction import (
    EvidenceExtractionAgent,
    _ExtractedClaim,
    _ExtractionOutput,
)
from app.agents.report import ReportAgent, _ReportOutput, _SectionDraft
from app.agents.research_planner import (
    ResearchPlannerAgent,
    _PlannerOutput,
    _PlannerSubQuestion,
)
from app.agents.source_discovery import (
    SourceDiscoveryAgent,
    _DiscoveryOutput,
    _DiscoveryQuery,
)
from app.agents.synthesis import SynthesisAgent, _FindingDraft, _SynthesisOutput
from app.media.composition.base import FakeCompositionService
from app.media.pipeline import MediaPipelineError
from app.media.tts.base import FakeTTSProvider
from app.media.visuals.base import FakeVisualProvider, VisualClip, VisualKind
from app.schemas.research_state import SourceType, SupportLevel
from app.services.composition import MediaDeps
from app.services.ingestion.base import FetchedContent
from app.services.ingestion.fakes import FakeFetchProvider
from app.services.ingestion.service import IngestionService
from app.services.llm.base import ModelRole
from app.services.llm.fakes import FakeProvider
from app.services.llm.router import ModelChoice, ModelRouter
from app.services.search.base import SearchResult
from app.services.search.fakes import FakeSearchProvider
from app.services.video import VideoArtifact, VideoPipeline, VideoPipelineError
from app.workflows.deep_research import ResearchDeps

# --- Fake-backed research deps (mirror tests/integration/test_pipeline_e2e) ---


def _router(output: object, role: ModelRole = ModelRole.PLANNING) -> ModelRouter:
    return ModelRouter(
        providers={"fake": FakeProvider([output])},
        policy={role: ModelChoice("fake", "fake-model")},
    )


def _planner() -> ResearchPlannerAgent:
    output = _PlannerOutput(
        goal="goal",
        sub_questions=[_PlannerSubQuestion(text=t) for t in ("q1", "q2")],
    )
    return ResearchPlannerAgent(_router(output))


def _discovery(n_sources: int = 2) -> SourceDiscoveryAgent:
    output = _DiscoveryOutput(queries=[_DiscoveryQuery(query="q", source_type=SourceType.WEB)])
    search = FakeSearchProvider(
        [
            SearchResult(url=f"https://s{i}.com", source_type=SourceType.WEB)
            for i in range(n_sources)
        ]
    )
    return SourceDiscoveryAgent(_router(output), search)


def _ingestion(n_sources: int = 2) -> IngestionService:
    by_url = {
        f"https://s{i}.com": FetchedContent(
            url=f"https://s{i}.com", content=f"<p>body {i}</p>".encode(), content_type="text/html"
        )
        for i in range(n_sources)
    }
    return IngestionService(FakeFetchProvider(by_url))


def _extractor(n_chunks: int = 2) -> EvidenceExtractionAgent:
    outputs = [
        _ExtractionOutput(claims=[_ExtractedClaim(claim=f"claim {i}", confidence=0.8)])
        for i in range(max(n_chunks, 1))
    ]
    return EvidenceExtractionAgent(
        ModelRouter(
            providers={"fake": FakeProvider(outputs)},
            policy={ModelRole.EXTRACTION: ModelChoice("fake", "fake-model")},
        )
    )


def _verifier() -> CrossVerificationAgent:
    output = _VerificationOutput(
        verdicts=[
            _VerdictDraft(
                claim="verdict",
                support_level=SupportLevel.SINGLE_SOURCE,
                confidence=0.7,
                supporting=[0],
            )
        ]
    )
    return CrossVerificationAgent(_router(output))


def _synthesizer() -> SynthesisAgent:
    output = _SynthesisOutput(
        findings=[_FindingDraft(statement="finding", supporting_verdicts=[0], sub_questions=[0, 1])]
    )
    return SynthesisAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


def _critic() -> EditorialCriticAgent:
    return EditorialCriticAgent(
        _router(_CritiqueOutput(issues=[], rationale="ok"), role=ModelRole.PLANNING)
    )


def _reporter() -> ReportAgent:
    output = _ReportOutput(
        title="t", abstract="a", sections=[_SectionDraft(heading="h", narrative="n", findings=[0])]
    )
    return ReportAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


# The seam that must be right: the strategist produces a NARRATIVE (not just a
# hook), so the creator packet is narratable and the media layer has a script.
_NARRATIVE_OUTLINE = "Hook line\nBody beat one\nClosing call to action"


def _strategist(outline: str = _NARRATIVE_OUTLINE) -> CreatorPacketAgent:
    output = _PacketOutput(
        narratives=[_NarrativeDraft(title="Why it matters", script_outline=outline, findings=[0])]
    )
    return CreatorPacketAgent(
        ModelRouter(
            providers={"fake": FakeProvider([output])},
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )


def _research_deps(strategist: CreatorPacketAgent | None = None) -> ResearchDeps:
    return ResearchDeps(
        planner=_planner(),
        discovery=_discovery(),
        ingestion=_ingestion(),
        extractor=_extractor(),
        verifier=_verifier(),
        synthesizer=_synthesizer(),
        critic=_critic(),
        reporter=_reporter(),
        strategist=strategist or _strategist(),
    )


def _media_deps(*, visuals: FakeVisualProvider | None = None) -> MediaDeps:
    return MediaDeps(
        tts=FakeTTSProvider(ms_per_char=10),
        composition=FakeCompositionService(),
        visuals=visuals,
        voice="narrator",
    )


# --- The end-to-end happy path -----------------------------------------------


def test_topic_to_video_artifact_end_to_end() -> None:
    pipeline = VideoPipeline(_research_deps(), _media_deps())
    artifact = asyncio.run(pipeline.create("fusion energy"))

    assert isinstance(artifact, VideoArtifact)
    assert artifact.topic == "fusion energy"
    assert artifact.narrative_title == "Why it matters"
    assert artifact.id.startswith("reel_")
    assert artifact.produced_via == "video:video"
    # The fake renderer mints a fake:// uri; duration mirrors the synthesized audio.
    assert artifact.video_uri.startswith("fake://composition/")
    assert artifact.duration_ms > 0
    assert artifact.width > 0 and artifact.height > 0
    # Re-join keys are populated so the artifact traces back through the chain.
    assert artifact.research_state_id
    assert artifact.creator_packet_id
    assert artifact.media_plan_id


def test_pipeline_passes_narrative_through_to_composition() -> None:
    composition = FakeCompositionService()
    pipeline = VideoPipeline(
        _research_deps(),
        MediaDeps(tts=FakeTTSProvider(ms_per_char=10), composition=composition),
    )
    asyncio.run(pipeline.create("topic"))
    # The media layer composed exactly once over the chosen narrative.
    assert len(composition.calls) == 1
    # No visual provider wired -> empty visuals (default-background render).
    assert composition.calls[0].visual_uris == []


def test_pipeline_retrieves_and_forwards_visuals_when_provider_wired() -> None:
    composition = FakeCompositionService()
    visuals = FakeVisualProvider(
        [
            VisualClip(
                uri="fake://broll/1.mp4",
                kind=VisualKind.VIDEO,
                width=1080,
                height=1920,
                produced_via="visuals:fake",
            )
        ]
    )
    pipeline = VideoPipeline(
        _research_deps(),
        MediaDeps(tts=FakeTTSProvider(), composition=composition, visuals=visuals),
    )
    asyncio.run(pipeline.create("topic"))
    # The visual provider was queried with the narrative title, and its uri was
    # forwarded to composition.
    assert visuals.calls[0].query == "Why it matters"
    assert composition.calls[0].visual_uris == ["fake://broll/1.mp4"]


def test_pipeline_bridges_visuals_through_the_sink() -> None:
    # When a VisualSink is wired (the live path), each retrieved remote uri is
    # mapped through it before composition — the bridge that turns a stock
    # provider's https uri into a file:// uri ffmpeg can resolve.
    composition = FakeCompositionService()
    visuals = FakeVisualProvider(
        [
            VisualClip(
                uri="https://cdn.example.com/broll.mp4",
                kind=VisualKind.VIDEO,
                width=1080,
                height=1920,
                produced_via="visuals:fake",
            )
        ]
    )

    def _sink(uri: str) -> str:
        return f"file:///local/{uri.rsplit('/', 1)[-1]}"

    pipeline = VideoPipeline(
        _research_deps(),
        MediaDeps(
            tts=FakeTTSProvider(),
            composition=composition,
            visuals=visuals,
            visual_sink=_sink,
        ),
    )
    asyncio.run(pipeline.create("topic"))
    assert composition.calls[0].visual_uris == ["file:///local/broll.mp4"]


# --- The handoff guards (the load-bearing failure modes) ---------------------


def test_pipeline_raises_when_research_yields_unnarratable_packet() -> None:
    # A strategist that emits no narrative -> the creator packet has no narrative
    # to render. The media layer raises MediaPipelineError; the pipeline does not
    # swallow it. (The CreatorPacketAgent itself requires *some* resolvable
    # element, so we give it a hook but no narrative.)
    from app.agents.creator_packet import _HookDraft

    hook_only = CreatorPacketAgent(
        ModelRouter(
            providers={
                "fake": FakeProvider([_PacketOutput(hooks=[_HookDraft(text="h", findings=[0])])])
            },
            policy={ModelRole.LONG_CONTEXT: ModelChoice("fake", "fake-model")},
        )
    )
    pipeline = VideoPipeline(_research_deps(hook_only), _media_deps())
    with pytest.raises(VideoPipelineError, match="no narrative options"):
        asyncio.run(pipeline.create("topic"))


def test_pipeline_raises_when_research_run_fails() -> None:
    # A planner whose model raises -> the workflow terminates FAILED (no packet).
    # The pipeline's guard must raise VideoPipelineError, not IndexError into an
    # empty packets list.
    class _Boom:
        name = "fake"

        async def complete_structured(self, **_: object) -> object:
            raise RuntimeError("planner exploded")

    failing_planner = ResearchPlannerAgent(
        ModelRouter(
            providers={"fake": _Boom()},  # type: ignore[dict-item]
            policy={ModelRole.PLANNING: ModelChoice("fake", "fake-model")},
        )
    )
    deps = _research_deps()
    deps = ResearchDeps(
        planner=failing_planner,
        discovery=deps.discovery,
        ingestion=deps.ingestion,
        extractor=deps.extractor,
        verifier=deps.verifier,
        synthesizer=deps.synthesizer,
        critic=deps.critic,
        reporter=deps.reporter,
        strategist=deps.strategist,
    )
    pipeline = VideoPipeline(deps, _media_deps())
    with pytest.raises(VideoPipelineError, match="did not complete"):
        asyncio.run(pipeline.create("topic"))


def test_pipeline_raises_on_out_of_range_narrative_index() -> None:
    pipeline = VideoPipeline(_research_deps(), _media_deps())
    with pytest.raises(VideoPipelineError, match="out of range"):
        asyncio.run(pipeline.create("topic", narrative_index=9))


def test_artifact_revalidates_strict() -> None:
    pipeline = VideoPipeline(_research_deps(), _media_deps())
    artifact = asyncio.run(pipeline.create("topic"))
    assert VideoArtifact.model_validate(artifact.model_dump())


# Sanity: the media layer's own empty-narrative error type is the one that
# surfaces for an all-blank script (documents the boundary the pipeline relies on).
def test_blank_narrative_surfaces_media_error() -> None:
    pipeline = VideoPipeline(_research_deps(_strategist("   \n  \n")), _media_deps())
    with pytest.raises(MediaPipelineError, match="no narratable script segments"):
        asyncio.run(pipeline.create("topic"))


# --- The keystone wiring (ADR 0050): live construction with a Kokoro-only TTS --


def test_build_video_pipeline_constructs_with_kokoro_only_no_tts_service_key() -> None:
    """The integration keystone: with LLM + search + stock configured but NO TTS
    service key, `build_video_pipeline` constructs (the local Kokoro default needs
    no account). Construction is offline — Kokoro loads the model lazily at synth
    time — so this never touches the network or the model files.
    """
    from pydantic import SecretStr

    from app.core.config import Settings
    from app.services.video import build_video_pipeline

    settings = Settings(  # type: ignore[call-arg]
        default_provider="openai-compatible",
        base_url="https://api.example.com/v1",
        api_key=SecretStr("sk-test"),
        search_provider="tavily",
        search_api_key=SecretStr("tvly-test"),
        stock_api_key=SecretStr("pexels-test"),
        tts_backend="kokoro",
        # No NVIDIA/HF/OpenAI TTS key set — Kokoro-only.
    )
    bundle = build_video_pipeline(settings)
    assert isinstance(bundle.pipeline, VideoPipeline)
    # The supervised TTS router actually reached the pipeline (no service key).
    assert bundle.pipeline._media_deps.tts.name == "supervised"
    # research model+search+fetch (3) + media model (1) + stock visuals (1) = 5;
    # Kokoro owns no client, so it adds nothing. Every closable can be drained.
    assert len(bundle.closables) == 5
    assert all(hasattr(c, "aclose") for c in bundle.closables)
