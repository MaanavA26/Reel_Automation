"""Hermetic tests for the `ClosedLoopRunner` driver loop (ADR 0054).

Fully offline: a tiny fake pipeline returns a hand-built `ProducedVideo` (so the
loop logic — gate / modes / budget / review / publish / feedback / shutdown — is
exercised without re-running research or ffmpeg), the publishing/analytics fakes
stand in for the network, and the timing seam is driven by a **fake clock + a
sleeper that advances it**, so the cadence is asserted with no real waiting
(mirroring ADR 0034's clock-free primitives).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.base import VideoStats
from app.analytics.fakes import FakeAnalyticsProvider
from app.media.pipeline import MediaPlan
from app.media.schemas import CaptionTrack, RenderedVideo, SynthesizedSpeech
from app.publishing.base import FakePublishingProvider
from app.publishing.youtube import PublishError
from app.review.record import ReviewStatus
from app.review.service import ReviewService
from app.safety.gate import PrePublishGate
from app.scheduler.closed_loop import (
    ClosedLoopRunner,
    LoopMode,
    PendingPublicationStore,
    StopSignal,
)
from app.scheduler.schedule import IntervalSchedule
from app.schemas.research_state import (
    Caveat,
    CaveatKind,
    Citation,
    CreatorPacket,
    NarrativeOption,
    Report,
    ResearchState,
    SourceType,
)
from app.services.budget.tracker import BudgetLimits, BudgetTracker
from app.services.video.pipeline import ProducedVideo, VideoArtifact

# --- builders ---------------------------------------------------------------


def _produced(
    topic: str,
    *,
    citations: int = 2,
    caveats: Sequence[Caveat] = (),
) -> ProducedVideo:
    """Hand-build a `ProducedVideo` for ``topic`` with a tunable safety surface.

    ``citations`` controls the grounding (>=2 distinct sources clears the gate's
    floor); ``caveats`` lets a test inject a DISPUTED_FINDING (→ BLOCK) or an
    UNRESOLVED_CRITIQUE (→ BLOCK without a disclaimer).
    """
    report = Report(
        title=f"{topic} report",
        abstract=f"abstract for {topic}",
        citations=[
            Citation(
                source_id=f"src_{i}",
                source_url=f"https://example.com/{i}",
                source_type=SourceType.WEB,
                title=f"Source {i}",
            )
            for i in range(citations)
        ],
        caveats=list(caveats),
        published_via="report:fake",
    )
    packet = CreatorPacket(
        report_id=report.id,
        narratives=[NarrativeOption(title=f"{topic} narrative", script_outline="beat one")],
        published_via="packet:fake",
    )
    video = RenderedVideo(
        video_uri=f"file:///renders/{topic}.mp4",
        duration_ms=30_000,
        width=1080,
        height=1920,
        produced_via="composition:fake",
    )
    plan = MediaPlan(
        source_packet_id=packet.id,
        narrative_title=packet.narratives[0].title,
        script_segments=["beat one", "beat two"],
        audio=SynthesizedSpeech(
            audio_uri="file:///renders/a.audio",
            duration_ms=30_000,
            voice="narrator",
            produced_via="tts:fake",
        ),
        captions=CaptionTrack(cues=[], produced_via="subs:fake"),
        video=video,
        produced_via="media:fake",
    )
    artifact = VideoArtifact(
        topic=topic,
        research_state_id="rs_x",
        creator_packet_id=packet.id,
        media_plan_id=plan.id,
        narrative_title=plan.narrative_title,
        video_uri=video.video_uri,
        duration_ms=video.duration_ms,
        width=video.width,
        height=video.height,
        produced_via="video:fake",
    )
    return ProducedVideo(
        artifact=artifact,
        research_state=ResearchState(topic=topic),
        report=report,
        packet=packet,
        media_plan=plan,
    )


class _FakePipeline:
    """A `VideoPipeline` stand-in returning scripted `ProducedVideo`s by topic.

    ``fail_topics`` raise from `create_bundle` so the loop's error-isolation
    (inherited from `BatchRunner`) is exercised.
    """

    def __init__(
        self,
        produced: dict[str, ProducedVideo],
        *,
        fail_topics: frozenset[str] = frozenset(),
    ) -> None:
        self._produced = produced
        self._fail_topics = fail_topics
        self.calls: list[str] = []

    async def create_bundle(self, topic: str, *, narrative_index: int = 0) -> ProducedVideo:
        self.calls.append(topic)
        if topic in self._fail_topics:
            raise RuntimeError(f"boom: {topic}")
        return self._produced[topic]


def _runner(
    pipeline: _FakePipeline,
    *,
    topics: Sequence[str],
    mode: LoopMode = LoopMode.SUPERVISED,
    reviews: ReviewService | None = None,
    pending: PendingPublicationStore | None = None,
    publisher: FakePublishingProvider | None = None,
    gate: PrePublishGate | None = None,
    budget: BudgetTracker | None = None,
    analytics: FakeAnalyticsProvider | None = None,
    batch_size: int = 5,
) -> tuple[ClosedLoopRunner, ReviewService, PendingPublicationStore, FakePublishingProvider]:
    reviews = reviews or ReviewService()
    pending = pending or PendingPublicationStore()
    publisher = publisher or FakePublishingProvider()

    async def _source() -> list[str]:
        return list(topics)

    runner = ClosedLoopRunner(
        pipeline=pipeline,  # type: ignore[arg-type]  # structural: only create_bundle is used
        gate=gate or PrePublishGate(),
        reviews=reviews,
        pending=pending,
        publisher=publisher,
        topic_source=_source,
        budget=budget or BudgetTracker(),
        mode=mode,
        analytics=analytics,
        batch_size=batch_size,
    )
    return runner, reviews, pending, publisher


# --- supervised vs autonomous (the ALLOW branch) ----------------------------


def test_supervised_holds_every_allow_video_and_publishes_nothing() -> None:
    pipeline = _FakePipeline({"t1": _produced("t1")})
    runner, reviews, pending, publisher = _runner(pipeline, topics=["t1"])

    report = asyncio.run(runner.run_once())

    assert report.produced == 1
    assert report.held_for_review == 1
    assert report.published_now == 0
    assert publisher.calls == []  # nothing auto-posts in supervised mode
    assert len(pending) == 1
    held = asyncio.run(reviews.list_records(status=ReviewStatus.PENDING_REVIEW))
    assert len(held) == 1


def test_autonomous_publishes_allow_video_immediately() -> None:
    pipeline = _FakePipeline({"t1": _produced("t1")})
    runner, _reviews, pending, publisher = _runner(
        pipeline, topics=["t1"], mode=LoopMode.AUTONOMOUS
    )

    report = asyncio.run(runner.run_once())

    assert report.produced == 1
    assert report.published_now == 1
    assert report.held_for_review == 0
    assert len(publisher.calls) == 1
    assert len(pending) == 0  # nothing held — it was posted


# --- BLOCK / REVIEW (mode-independent) --------------------------------------


@pytest.mark.parametrize("mode", [LoopMode.SUPERVISED, LoopMode.AUTONOMOUS])
def test_block_drops_and_never_publishes(mode: LoopMode) -> None:
    disputed = Caveat(kind=CaveatKind.DISPUTED_FINDING, detail="contradicted")
    pipeline = _FakePipeline({"t1": _produced("t1", caveats=[disputed])})
    runner, _reviews, pending, publisher = _runner(pipeline, topics=["t1"], mode=mode)

    report = asyncio.run(runner.run_once())

    assert report.blocked == 1
    assert report.published_now == 0
    assert report.held_for_review == 0
    assert publisher.calls == []
    assert len(pending) == 0  # a blocked video is not even held


@pytest.mark.parametrize("mode", [LoopMode.SUPERVISED, LoopMode.AUTONOMOUS])
def test_review_holds_in_both_modes(mode: LoopMode) -> None:
    # One distinct source < the gate's floor of 2 → REVIEW.
    pipeline = _FakePipeline({"t1": _produced("t1", citations=1)})
    runner, reviews, pending, publisher = _runner(pipeline, topics=["t1"], mode=mode)

    report = asyncio.run(runner.run_once())

    assert report.held_for_review == 1
    assert report.published_now == 0
    assert publisher.calls == []
    held = asyncio.run(reviews.list_records(status=ReviewStatus.PENDING_REVIEW))
    assert len(held) == 1
    assert len(pending) == 1


# --- approved -> publish wiring ---------------------------------------------


def test_approved_hold_publishes_on_next_tick() -> None:
    pipeline = _FakePipeline({"t1": _produced("t1")})
    runner, reviews, pending, publisher = _runner(pipeline, topics=["t1"])

    # Tick 1: supervised hold.
    asyncio.run(runner.run_once())
    held = asyncio.run(reviews.list_records(status=ReviewStatus.PENDING_REVIEW))
    assert len(held) == 1
    assert publisher.calls == []

    # A human approves via the review gate.
    asyncio.run(reviews.approve(held[0].id, reason="looks good"))

    # Tick 2: publish_approved (run at the top of run_once) posts it, once.
    pipeline_2 = _FakePipeline({})  # no new topics this tick
    runner._pipeline = pipeline_2  # type: ignore[attr-defined]

    async def _no_topics() -> list[str]:
        return []

    runner._topic_source = _no_topics  # type: ignore[attr-defined]
    report = asyncio.run(runner.run_once())

    assert report.published_now == 1
    assert len(publisher.calls) == 1
    assert len(pending) == 0  # popped on publish

    # A third tick must NOT re-publish (idempotent — already popped).
    report3 = asyncio.run(runner.run_once())
    assert report3.published_now == 0
    assert len(publisher.calls) == 1


def test_publish_approved_retries_on_failure() -> None:
    class _FailingPublisher(FakePublishingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        async def publish(self, *, video, target):  # type: ignore[no-untyped-def]
            if self.fail_next:
                self.fail_next = False
                raise PublishError("transient upload failure")
            return await super().publish(video=video, target=target)

    pipeline = _FakePipeline({"t1": _produced("t1")})
    publisher = _FailingPublisher()
    runner, reviews, pending, _pub = _runner(pipeline, topics=["t1"], publisher=publisher)

    asyncio.run(runner.run_once())  # hold
    held = asyncio.run(reviews.list_records(status=ReviewStatus.PENDING_REVIEW))
    asyncio.run(reviews.approve(held[0].id))

    # First flush fails → the hold is re-stashed for retry.
    n1 = asyncio.run(runner.publish_approved())
    assert n1 == 0
    assert len(pending) == 1

    # Second flush succeeds.
    n2 = asyncio.run(runner.publish_approved())
    assert n2 == 1
    assert len(pending) == 0


# --- budget guardrail -------------------------------------------------------


def test_budget_ceiling_skips_production_without_crashing() -> None:
    # per_run budget of 2.0 with a 1.0 per-video estimate allows 2 produces; the
    # 3rd is skipped (BudgetExceededError captured by BatchRunner), not crashed.
    budget = BudgetTracker(limits=BudgetLimits(per_run=2.0))
    produced = {t: _produced(t) for t in ("t1", "t2", "t3")}
    pipeline = _FakePipeline(produced)
    runner, _r, _p, _pub = _runner(
        pipeline, topics=["t1", "t2", "t3"], budget=budget, mode=LoopMode.SUPERVISED
    )

    report = asyncio.run(runner.run_once())

    assert report.produced == 2
    assert report.skipped_budget == 1


# --- error isolation --------------------------------------------------------


def test_one_failing_topic_does_not_abort_the_batch() -> None:
    produced = {"ok1": _produced("ok1"), "ok2": _produced("ok2")}
    pipeline = _FakePipeline(produced, fail_topics=frozenset({"bad"}))
    runner, _r, _p, _pub = _runner(pipeline, topics=["ok1", "bad", "ok2"], mode=LoopMode.AUTONOMOUS)

    report = asyncio.run(runner.run_once())

    assert report.produced == 2  # bad is captured, not counted as produced
    assert report.published_now == 2  # both good videos published


def test_publish_failure_is_isolated_in_autonomous_mode() -> None:
    class _AlwaysFailPublisher(FakePublishingProvider):
        async def publish(self, *, video, target):  # type: ignore[no-untyped-def]
            raise PublishError("nope")

    pipeline = _FakePipeline({"t1": _produced("t1")})
    runner, _r, _p, _pub = _runner(
        pipeline, topics=["t1"], mode=LoopMode.AUTONOMOUS, publisher=_AlwaysFailPublisher()
    )

    report = asyncio.run(runner.run_once())  # must not raise

    assert report.publish_failures == 1
    assert report.published_now == 0


# --- analytics feedback steers priority -------------------------------------


def test_analytics_feedback_prioritizes_proven_topics() -> None:
    # Publish two topics in autonomous mode, then a second tick fetches stats and
    # ranks them; the higher-performing topic should enqueue at a lower priority.
    analytics = FakeAnalyticsProvider(
        {
            "fakepost1": VideoStats(
                post_id="fakepost1", views=100_000, likes=10_000, fetched_via="analytics:fake"
            ),
            "fakepost2": VideoStats(
                post_id="fakepost2", views=10, likes=0, fetched_via="analytics:fake"
            ),
        }
    )
    produced = {"hot": _produced("hot"), "cold": _produced("cold")}
    pipeline = _FakePipeline(produced)
    runner, _r, _p, _pub = _runner(
        pipeline,
        topics=["hot", "cold"],
        mode=LoopMode.AUTONOMOUS,
        analytics=analytics,
    )

    asyncio.run(runner.run_once())  # publishes both; records post ids
    priorities = asyncio.run(runner._feedback_priorities())  # type: ignore[attr-defined]

    assert priorities  # feedback produced a priority map
    assert priorities["hot"] < priorities["cold"]  # proven topic jumps the queue


# --- run_forever: clock injection + graceful shutdown -----------------------


def test_run_forever_uses_injected_clock_and_stops_gracefully() -> None:
    pipeline = _FakePipeline({"t1": _produced("t1")})
    runner, _r, _p, publisher = _runner(
        pipeline, topics=["t1"], mode=LoopMode.AUTONOMOUS, batch_size=1
    )

    async def scenario() -> None:
        clock = {"now": datetime(2026, 1, 1, tzinfo=UTC)}
        stop = StopSignal()
        ticks = {"count": 0}

        def _now() -> datetime:
            return clock["now"]

        async def _sleep(seconds: float) -> None:
            # Advance the fake clock instead of really waiting, and stop after the
            # loop has run a couple of ticks (otherwise it would loop forever).
            clock["now"] = clock["now"] + timedelta(seconds=seconds)
            ticks["count"] += 1
            if ticks["count"] >= 2:
                stop.stop()

        schedule = IntervalSchedule(interval=timedelta(seconds=3600), anchor=clock["now"])
        await runner.run_forever(schedule, now=_now, sleep=_sleep, stop=stop)

    asyncio.run(scenario())

    # The loop advanced the clock via the injected sleeper (no real waiting) and
    # exited cleanly once stop was requested.
    assert len(publisher.calls) >= 1


def test_stop_signal_interrupts_a_long_sleep() -> None:
    async def scenario() -> bool:
        stop = StopSignal()

        async def _real_sleep(seconds: float) -> None:
            await asyncio.sleep(seconds)  # a genuinely long sleep

        # Schedule a stop shortly after, then race it against a 10_000s sleep; the
        # stop must interrupt the wait far inside the outer 1s guard.
        async def _stop_soon() -> None:
            await asyncio.sleep(0)
            stop.stop()

        stopper = asyncio.ensure_future(_stop_soon())
        await asyncio.wait_for(stop.sleep_or_stop(10_000.0, _real_sleep), timeout=1.0)
        await stopper
        return stop.is_set

    assert asyncio.run(scenario()) is True
