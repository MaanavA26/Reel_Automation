"""`ClosedLoopRunner` — the deferred driver loop that closes the automation loop.

This module realizes the long-lived driver loop ADR 0034 explicitly deferred (see
the `app.scheduler` package docstring): the integrator that composes the three
pure scheduler primitives (`Schedule` + `TopicQueue` + `BatchRunner`) with the
real `VideoPipeline`, the `PrePublishGate`, the human-review gate, the publishing
fabric, and the analytics feedback loop into one unattended cadence. Per
CLAUDE.md §4 it is a deterministic **tool/service**, not an agent: it makes no
judgment of its own. Every reasoning step already happened upstream (topic
selection + the strategist agents decided *what* to make; the safety gate's
policy and the human's sign-off decide *whether* to post); this loop only
*sequences* those decisions procedurally.

The loop (one tick)
-------------------
``run_once`` is the whole loop body and contains **no clock and no sleeping**, so
the entire policy — publish-approved, source, enqueue, drain, produce, gate, act,
record — is hermetically testable with no real waiting (mirroring how
`schedule.py`/`runner.py` stay clock-free; ADR 0034)::

    publish any human-approved holds (from a prior tick)
      → source topics → enqueue (priority steered by analytics feedback)
      → drain a batch → BatchRunner.run_batch(produce=VideoPipeline.create_bundle)
      → for each produced video: PrePublishGate.evaluate
          • BLOCK  → drop + log (never auto-post disputed content)
          • REVIEW → submit to the review gate (PENDING_REVIEW); hold
          • ALLOW  → supervised: submit (hold for a human); autonomous: publish now
      → record published outcomes for the analytics feedback loop

``run_forever`` is the thin wrapper that binds the only real-clock/real-sleep
seam — both **injected** (``now`` / ``sleep``), so production passes the wall
clock + ``asyncio.sleep`` while tests pass a fake clock + a sleeper that advances
it. A `StopSignal` interrupts the wait so a shutdown does not block mid-sleep.

Two modes (config-selected; the load-bearing design decision)
-------------------------------------------------------------
Only the **ALLOW** branch differs between modes — BLOCK always drops, REVIEW
always holds:

* ``SUPERVISED`` (the safe default): even an ALLOW video is routed through the
  human-review gate; **nothing auto-posts**. A human approves via the existing
  reviews API, and a later tick (or an explicit `publish_approved` call) posts it.
* ``AUTONOMOUS`` (opt-in, last-mile/live-key gated): an ALLOW video within budget
  is published immediately; only REVIEW holds for a human. This is the truly
  unattended income loop and **auto-posts to real platforms**, so it defaults OFF.

See ADR 0054.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.analytics.base import AnalyticsProvider, VideoStats
from app.analytics.feedback import score_topics
from app.publishing.base import PublishingProvider, PublishResult, PublishTarget
from app.review.record import ReviewStatus
from app.review.service import ReviewService
from app.safety.gate import PrePublishGate, PublishCandidate
from app.safety.verdict import SafetyDecision
from app.scheduler.queue import TopicQueue
from app.scheduler.runner import BatchRunner
from app.scheduler.schedule import Schedule
from app.services.budget.tracker import BudgetExceededError, BudgetTracker
from app.services.video.pipeline import ProducedVideo, VideoPipeline

logger = logging.getLogger(__name__)

#: An injected clock returning a tz-aware ``datetime`` (mirrors the budget
#: tracker / eval harness convention). Production passes ``datetime.now(UTC)``;
#: tests pass a scripted clock so the cadence is asserted with no real waiting.
Now = Callable[[], datetime]

#: An injected async sleeper. Production passes ``asyncio.sleep``; tests pass a
#: sleeper that advances the fake clock so the loop progresses without real time.
Sleep = Callable[[float], Awaitable[None]]

#: Sources the topics to enqueue each tick (already de-duped/ranked upstream by
#: `topics.selection.select_topics` — this loop does not re-rank, it only steers
#: the *priority* via the analytics feedback signal). Async to match the I/O-bound
#: trend-provider contract; returns the ranked topic strings for this tick.
TopicSource = Callable[[], Awaitable[Sequence[str]]]

#: A flat per-video cost estimate charged to the loop's own `BudgetTracker`
#: before producing each topic. The end-to-end video cost is many LLM calls whose
#: individual spend the model-fabric budget decorator already meters; this is a
#: coarse loop-level guardrail (count/cost cap) so an unattended run cannot
#: produce/post unboundedly, not a precise invoice. See ADR 0054.
_DEFAULT_VIDEO_COST_ESTIMATE = 1.0


class LoopMode(StrEnum):
    """How the loop treats a safety-ALLOW video (the only mode-dependent branch).

    Lowercase values to match `SafetyDecision` / `ReviewStatus`. ``SUPERVISED`` is
    the safe default (every video held for a human); ``AUTONOMOUS`` auto-posts an
    ALLOW video and is the opt-in, live-key-gated unattended loop (ADR 0054).
    """

    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class PendingPublication:
    """A produced video held for a human sign-off, plus its publishable payload.

    The `ReviewRecord` references its subject only by id/label — it carries **no
    payload** — so the loop cannot reconstruct the `RenderedVideo`/`PublishTarget`
    from an approved record alone. This stash keeps the publishable bundle keyed
    by the review's ``subject_id`` (the `VideoArtifact.id`) so an approval on a
    later tick can publish without re-running the pipeline. Frozen: the held
    payload is immutable once stashed.
    """

    topic: str
    produced: ProducedVideo
    target: PublishTarget


class PendingPublicationStore:
    """Process-local registry of videos awaiting a human approve→publish.

    Keyed by the review subject id (``VideoArtifact.id``). Single-process and
    **non-durable** by design — the same model as `JobStore` (ADR 0031) and
    `ReviewService` (ADR 0051): a restart loses pending holds. Guarded by one
    `asyncio.Lock` for honesty/future-proofing (the loop runs on one event loop),
    symmetric with `ReviewService`.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingPublication] = {}
        self._lock = asyncio.Lock()

    async def put(self, subject_id: str, pending: PendingPublication) -> None:
        """Stash ``pending`` under ``subject_id`` (the review subject / artifact id)."""
        async with self._lock:
            self._pending[subject_id] = pending

    async def pop(self, subject_id: str) -> PendingPublication | None:
        """Remove and return the held payload for ``subject_id``, or ``None``.

        Pop (not peek) so a published hold is removed atomically — the next tick's
        approved-scan will not re-publish it (idempotency on the loop side).
        """
        async with self._lock:
            return self._pending.pop(subject_id, None)

    def __len__(self) -> int:
        return len(self._pending)


@dataclass
class StopSignal:
    """A cooperative shutdown flag the driver loop checks and waits on.

    `stop` sets the flag *and* wakes a loop blocked in its inter-tick wait, so a
    shutdown finishes the in-flight tick and exits cleanly without blocking for
    the remainder of a (possibly hours-long) sleep. Built lazily on first use so
    the `asyncio.Event` binds to the running loop (the same loop-binding care
    `BatchRunner` takes with its semaphore).
    """

    _stopped: bool = field(default=False, init=False)
    _event: asyncio.Event | None = field(default=None, init=False)

    @property
    def is_set(self) -> bool:
        return self._stopped

    def _ensure_event(self) -> asyncio.Event:
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    def stop(self) -> None:
        """Request shutdown and wake any loop blocked in its inter-tick wait."""
        self._stopped = True
        self._ensure_event().set()

    async def sleep_or_stop(self, seconds: float, sleep: Sleep) -> None:
        """Sleep ``seconds`` via the **injected** sleeper, returning early on `stop`.

        Races the injected ``sleep(seconds)`` against the stop event, so the wait
        uses the *injected* clock seam (a test sleeper never really waits) yet a
        `stop()` interrupts it immediately rather than blocking for the full
        duration. The injected sleeper is what keeps `run_forever` hermetic — the
        only timing primitive, bound to ``asyncio.sleep`` in production and to a
        fake (clock-advancing) sleeper in tests.
        """
        if seconds <= 0 or self._stopped:
            return
        event = self._ensure_event()
        sleep_task = asyncio.ensure_future(sleep(seconds))
        stop_task = asyncio.ensure_future(event.wait())
        try:
            await asyncio.wait({sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleep_task, stop_task):
                if not task.done():
                    task.cancel()


@dataclass(frozen=True)
class TickReport:
    """An observable summary of one `run_once` tick (for logging/assertions).

    Captures what the tick did without re-deriving it from logs: how many topics
    were produced, and the per-decision counts. ``published_now`` are videos
    posted this tick (autonomous ALLOW + approved holds); ``held_for_review`` were
    submitted to the human gate; ``blocked`` were dropped; ``skipped_budget`` were
    not produced/posted because a ceiling was hit. Pure value object.
    """

    produced: int = 0
    published_now: int = 0
    held_for_review: int = 0
    blocked: int = 0
    skipped_budget: int = 0
    publish_failures: int = 0


class ClosedLoopRunner:
    """Composes the scheduler primitives + real seams into the unattended loop.

    All collaborators are injected (factory-closure DI, the repo convention) so
    the loop runs hermetically with fakes and is config-gated for a live run via
    `app.services.composition`. The runner owns no clock — `run_once` is pure of
    timing; `run_forever` takes the injected ``now``/``sleep``.

    Args:
        pipeline: the real `VideoPipeline` (produce target — its `create_bundle`).
        gate: the deterministic `PrePublishGate` (the automated ALLOW/REVIEW/BLOCK).
        reviews: the human-review gate (`ReviewService`) — the HOLD point.
        pending: the `PendingPublicationStore` stashing held payloads by subject id.
        publisher: the `PublishingProvider` that uploads a `RenderedVideo`.
        topic_source: yields the ranked topics to enqueue each tick.
        budget: the loop's own `BudgetTracker` (per-video cost/count ceiling).
        mode: `LoopMode.SUPERVISED` (default, nothing auto-posts) or `AUTONOMOUS`.
        analytics: optional `AnalyticsProvider` for the feedback loop (``None``
            disables feedback — the loop still produces/publishes).
        queue: the `TopicQueue` backlog (created fresh if not injected).
        batch_size: how many topics to drain + produce per tick.
        max_concurrency: the `BatchRunner` concurrency cap.
        video_cost_estimate: the flat per-video cost charged to ``budget``.
        privacy_status: the platform privacy for published videos (default
            ``"private"`` — autonomous public posting is opt-in via config).
    """

    name = "closed-loop"

    def __init__(
        self,
        *,
        pipeline: VideoPipeline,
        gate: PrePublishGate,
        reviews: ReviewService,
        pending: PendingPublicationStore,
        publisher: PublishingProvider,
        topic_source: TopicSource,
        budget: BudgetTracker,
        mode: LoopMode = LoopMode.SUPERVISED,
        analytics: AnalyticsProvider | None = None,
        queue: TopicQueue | None = None,
        batch_size: int = 3,
        max_concurrency: int = 2,
        video_cost_estimate: float = _DEFAULT_VIDEO_COST_ESTIMATE,
        privacy_status: str = "private",
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._pipeline = pipeline
        self._gate = gate
        self._reviews = reviews
        self._pending = pending
        self._publisher = publisher
        self._topic_source = topic_source
        self._budget = budget
        self._mode = mode
        self._analytics = analytics
        self._queue = queue if queue is not None else TopicQueue()
        self._batch_size = batch_size
        self._video_cost_estimate = video_cost_estimate
        self._privacy_status = privacy_status
        # Reuse BatchRunner's per-topic error isolation unchanged: one bad topic
        # never aborts the batch. The produce target is the rich-bundle method.
        self._runner: BatchRunner[ProducedVideo] = BatchRunner(
            self._produce, max_concurrency=max_concurrency
        )
        # topic -> the post ids it produced, for the cross-tick analytics loop.
        self._posts_by_topic: dict[str, list[str]] = {}

    # -- the loop body (no clock, no sleeping) ------------------------------

    async def run_once(self) -> TickReport:
        """Run one full tick of the loop and return an observable `TickReport`.

        Contains **no sleeping** — the cadence is `run_forever`'s concern — so the
        entire policy is testable with no clock. Order: (1) publish any approved
        holds from a prior tick; (2) source + enqueue topics; (3) drain a batch and
        produce concurrently (error-isolated); (4) gate + act on each produced
        video per the configured mode; (5) feed analytics back into topic priority.
        """
        approved_published = await self.publish_approved()

        topics = await self._source_and_enqueue()
        batch = self._queue.drain(self._batch_size)
        if not batch and not approved_published:
            logger.info("closed-loop tick: no topics queued, nothing produced")

        result = await self._runner.run_batch(batch)
        tally = self._tally_init(
            produced=len(result.succeeded),
            published_now=approved_published,
        )

        for outcome in result.results:
            if outcome.error is not None:
                self._note_produce_error(outcome.topic, outcome.error, tally)
                continue
            assert outcome.value is not None  # ok() guarantees value is set
            await self._gate_and_act(outcome.value, tally)

        await self._feed_back_analytics(topics)
        report = self._tally_finalize(tally)
        logger.info("closed-loop tick complete: %s", report)
        return report

    async def publish_approved(self) -> int:
        """Publish every human-approved hold still in the pending store.

        The approved→publish wiring (ADR 0051's deferred follow-up): scans the
        review gate for ``APPROVED`` records and publishes any whose subject is
        still pending, popping each on success so a later tick never re-publishes.
        Exposed as a method (not only run from `run_once`) so a CLI/endpoint can
        trigger an out-of-band flush without a full tick — the publish network I/O
        stays out of a request handler. Returns the count published this call.

        Per-record failures are isolated and logged (a held payload is *not*
        popped on failure, so it is retried next tick) — one bad upload never
        aborts the flush.
        """
        approved = await self._reviews.list_records(status=ReviewStatus.APPROVED)
        published = 0
        for record in approved:
            pending = await self._pending.pop(record.subject_id)
            if pending is None:
                continue  # already published (or never ours) — idempotent
            try:
                await self._publish(pending.topic, pending.produced, pending.target)
                published += 1
            except Exception:  # isolate: one failed upload must not abort the flush
                logger.exception(
                    "publishing approved review %s (subject %s) failed; will retry next tick",
                    record.id,
                    record.subject_id,
                )
                # Re-stash so the next approved-scan retries (pop already removed it).
                await self._pending.put(record.subject_id, pending)
        return published

    # -- produce ------------------------------------------------------------

    async def _produce(self, topic: str) -> ProducedVideo:
        """Produce one topic's rich bundle, charging the loop budget first.

        Charges the flat per-video estimate to the loop's `BudgetTracker` *before*
        producing, so a `BudgetExceededError` is raised before the (expensive) run
        — and, because `BatchRunner` captures it into `TopicResult.error`, surfaces
        as a skipped topic rather than crashing the loop. The video pipeline's own
        errors propagate the same way.
        """
        self._budget.charge(self._video_cost_estimate)
        return await self._pipeline.create_bundle(topic)

    # -- gate + act ---------------------------------------------------------

    async def _gate_and_act(self, produced: ProducedVideo, tally: dict[str, int]) -> None:
        """Evaluate the safety gate and act per decision + mode.

        BLOCK → drop + log (both modes). REVIEW → hold for a human (both modes).
        ALLOW → supervised: hold; autonomous: publish now (budget permitting).
        """
        candidate = _build_candidate(produced)
        verdict = self._gate.evaluate(produced.report, produced.packet, candidate)
        artifact = produced.artifact

        if verdict.decision is SafetyDecision.BLOCK:
            logger.warning(
                "closed-loop: BLOCK on %r (artifact %s) — dropping, not publishing. reasons=%s",
                artifact.topic,
                artifact.id,
                [r.kind.value for r in verdict.reasons],
            )
            tally["blocked"] += 1
            return

        if verdict.decision is SafetyDecision.REVIEW or self._mode is LoopMode.SUPERVISED:
            await self._hold_for_review(produced, verdict_decision=verdict.decision)
            tally["held_for_review"] += 1
            return

        # AUTONOMOUS + ALLOW: auto-publish if the budget permits the post.
        target = _build_target(produced, privacy_status=self._privacy_status)
        try:
            self._budget.charge(self._video_cost_estimate)
        except BudgetExceededError as exc:
            logger.info(
                "closed-loop: budget exhausted before auto-publishing %r — holding for review (%s)",
                artifact.topic,
                exc,
            )
            await self._hold_for_review(produced, verdict_decision=verdict.decision)
            tally["skipped_budget"] += 1
            tally["held_for_review"] += 1
            return
        try:
            await self._publish(artifact.topic, produced, target)
            tally["published_now"] += 1
        except Exception:  # isolate: a publish failure must never crash the loop
            logger.exception(
                "closed-loop: auto-publishing %r (artifact %s) failed",
                artifact.topic,
                artifact.id,
            )
            tally["publish_failures"] += 1

    async def _hold_for_review(
        self, produced: ProducedVideo, *, verdict_decision: SafetyDecision
    ) -> None:
        """Submit a video to the human-review gate and stash its publish payload.

        Submits a `PENDING_REVIEW` record keyed by the artifact id, then stashes
        the publishable bundle in the pending store under the same id so an
        approval can publish later without re-running the pipeline (ADR 0054 §3).
        """
        artifact = produced.artifact
        record = await self._reviews.submit(
            artifact.id,
            subject_label=f"{artifact.narrative_title} ({verdict_decision.value})",
        )
        target = _build_target(produced, privacy_status=self._privacy_status)
        await self._pending.put(
            artifact.id,
            PendingPublication(topic=artifact.topic, produced=produced, target=target),
        )
        logger.info(
            "closed-loop: held %r (artifact %s) for human review as %s",
            artifact.topic,
            artifact.id,
            record.id,
        )

    # -- publish + analytics ------------------------------------------------

    async def _publish(
        self, topic: str, produced: ProducedVideo, target: PublishTarget
    ) -> PublishResult:
        """Upload one rendered video and record the post id for the feedback loop."""
        result = await self._publisher.publish(video=produced.media_plan.video, target=target)
        self._posts_by_topic.setdefault(topic, []).append(result.post_id)
        logger.info("closed-loop: published %r as %s (%s)", topic, result.post_id, result.url)
        return result

    async def _source_and_enqueue(self) -> Sequence[str]:
        """Fetch this tick's ranked topics and enqueue them (priority steered).

        The priority is steered by the analytics feedback loop: a topic that has
        performed well in earlier ticks is enqueued ahead of an unproven one. A
        topic with no recorded performance gets the default priority (0).
        """
        topics = await self._topic_source()
        priorities = await self._feedback_priorities()
        for topic in topics:
            self._queue.enqueue(topic, priority=priorities.get(topic, 0))
        return topics

    async def _feedback_priorities(self) -> dict[str, int]:
        """Map analytics-derived topic scores to enqueue priorities (lower = first).

        Cross-tick by necessity (a just-posted video has no views yet; the
        advisor's bound): fetch stats for known posts, `score_topics`, then map a
        higher score to a *lower* priority integer so a proven topic jumps the
        queue. Returns an empty map when analytics is disabled or nothing has been
        posted — the loop then enqueues at the default priority.
        """
        if self._analytics is None or not self._posts_by_topic:
            return {}
        stats_by_topic: dict[str, list[VideoStats]] = {}
        for topic, post_ids in self._posts_by_topic.items():
            collected: list[VideoStats] = []
            for post_id in post_ids:
                try:
                    collected.append(await self._analytics.fetch_stats(post_id=post_id))
                except Exception:  # a stats fetch failure must not abort the tick
                    logger.warning(
                        "closed-loop: fetching stats for post %s failed; skipping", post_id
                    )
            if collected:
                stats_by_topic[topic] = collected
        if not stats_by_topic:
            return {}
        # Best-scoring topic gets priority -rank (most negative = first). The
        # ranking is deterministic (score desc, topic asc) so priorities are stable.
        ranked = score_topics(stats_by_topic)
        return {score.topic: -(len(ranked) - rank) for rank, score in enumerate(ranked)}

    async def _feed_back_analytics(self, topics: Sequence[str]) -> None:
        """Hook for end-of-tick analytics work (priority steering happens at enqueue).

        Kept as an explicit, named step so the ADR's "feed analytics back into
        topic selection" stage is visible in the loop body even though the actual
        steering is applied at enqueue time (`_feedback_priorities`). No-op today
        beyond a debug line; a future durable feedback store would hook in here.
        """
        if self._analytics is not None and self._posts_by_topic:
            logger.debug(
                "closed-loop: analytics feedback active over %d topic(s)",
                len(self._posts_by_topic),
            )

    # -- the thin timing wrapper (the one real-clock seam) ------------------

    async def run_forever(
        self,
        schedule: Schedule,
        *,
        now: Now,
        sleep: Sleep,
        stop: StopSignal | None = None,
    ) -> None:
        """Drive `run_once` on ``schedule``'s cadence until `stop` is requested.

        The only place a real clock + real sleeping are bound — both **injected**:
        production passes ``datetime.now(UTC)`` + ``asyncio.sleep``; tests pass a
        fake clock + a sleeper that advances it, so the cadence is asserted with no
        real waiting. Each iteration computes the next fire instant, waits until it
        (interruptibly), then runs one tick. A `stop()` finishes the in-flight tick
        and exits cleanly; the wait is interrupted so a shutdown never blocks for a
        full inter-tick sleep.
        """
        signal = stop if stop is not None else StopSignal()
        while not signal.is_set:
            reference = now()
            fire_at = schedule.next_run_after(reference)
            # Clamp to >= 0: a fire time already past (clock drift / a slow tick)
            # means "run now", never a negative sleep.
            delay = max((fire_at - reference).total_seconds(), 0.0)
            await signal.sleep_or_stop(delay, sleep)
            if signal.is_set:
                break
            await run_once_guarded(self.run_once)

    # -- tally helpers ------------------------------------------------------

    @staticmethod
    def _tally_init(*, produced: int, published_now: int) -> dict[str, int]:
        return {
            "produced": produced,
            "published_now": published_now,
            "held_for_review": 0,
            "blocked": 0,
            "skipped_budget": 0,
            "publish_failures": 0,
        }

    @staticmethod
    def _note_produce_error(topic: str, error: BaseException, tally: dict[str, int]) -> None:
        """Classify a captured produce error: a budget skip vs a genuine failure."""
        if isinstance(error, BudgetExceededError):
            logger.info("closed-loop: skipped producing %r — budget exhausted (%s)", topic, error)
            tally["skipped_budget"] += 1
        else:
            logger.warning("closed-loop: producing %r failed: %s", topic, error)

    @staticmethod
    def _tally_finalize(tally: dict[str, int]) -> TickReport:
        return TickReport(
            produced=tally["produced"],
            published_now=tally["published_now"],
            held_for_review=tally["held_for_review"],
            blocked=tally["blocked"],
            skipped_budget=tally["skipped_budget"],
            publish_failures=tally["publish_failures"],
        )


async def run_once_guarded(run_once: Callable[[], Awaitable[TickReport]]) -> TickReport | None:
    """Run one tick, capturing any unexpected error so the driver loop survives.

    `run_once` already isolates per-topic and per-publish failures, but a defect
    in the loop body itself (an unexpected error outside those guards) must not
    kill a long-lived `run_forever`. This wrapper captures it, logs it, and lets
    the loop proceed to the next tick — the loop-level analogue of `BatchRunner`'s
    per-topic isolation. Returns the tick's report, or ``None`` on a captured error.
    """
    try:
        return await run_once()
    except Exception:  # the loop must outlive a single tick's unexpected failure
        logger.exception("closed-loop: a tick failed unexpectedly; continuing to the next tick")
        return None


def _build_candidate(produced: ProducedVideo) -> PublishCandidate:
    """Map a produced video to the `PrePublishGate`'s `PublishCandidate` input.

    The deterministic projection from the produced objects onto the gate's third
    input (capability-before-wiring: nothing constructed a `PublishCandidate`
    before this milestone). The script surface is the media plan's narration beats
    joined (what the video actually says); the title/description come from the
    chosen narrative. ``disclaimer`` is deliberately left ``None``: the loop
    attaches no disclaimer, so an `UNRESOLVED_CRITIQUE` report BLOCKs by default
    (the safe direction — ADR 0054 / ADR 0041).
    """
    plan = produced.media_plan
    return PublishCandidate(
        packet_id=produced.packet.id,
        title=plan.narrative_title,
        description=produced.report.abstract,
        script_text="\n".join(plan.script_segments),
        disclaimer=None,
    )


def _build_target(produced: ProducedVideo, *, privacy_status: str) -> PublishTarget:
    """Map a produced video to the publishing fabric's `PublishTarget`.

    The deterministic projection onto the "what to post" payload: the chosen
    narrative's title, the report abstract as the description. ``privacy_status``
    is injected (default ``"private"`` upstream) so a loop never accidentally
    posts publicly — public posting is the autonomous, live-key-gated opt-in.
    """
    plan = produced.media_plan
    return PublishTarget(
        title=plan.narrative_title,
        description=produced.report.abstract,
        privacy_status=privacy_status,
    )
