"""Run the closed-loop automation runner — the unattended driver loop (ADR 0054).

The command-line surface of the `ClosedLoopRunner`: it builds the loop from
environment configuration and drives it on the configured cadence until
interrupted (Ctrl-C / SIGTERM), gracefully finishing the in-flight tick. This is
the long-lived process that realizes the driver loop ADR 0034 deferred.

Usage (from the ``backend/`` directory)::

    cp .env.example .env          # set LLM + search + TTS + trends + publish keys
    python -m app.cli.run_loop            # run forever on the configured cadence
    python -m app.cli.run_loop --once     # run a single tick and exit (smoke test)

Modes (``REEL_AUTOMATION_LOOP_MODE``):

* ``supervised`` (default, safe) — every produced video is held for a human to
  approve via the reviews API; **nothing auto-posts**.
* ``autonomous`` (opt-in) — a safety-ALLOW video within budget is **auto-posted
  to the real platform**; only REVIEW items hold for a human. Last-mile,
  live-key-gated — leave OFF unless you intend unattended publishing.

A live run needs everything `make_video` needs (LLM + search + TTS + ffmpeg)
plus a trends key (``REEL_AUTOMATION_TRENDS_API_KEY``) and, for publishing, a
platform token (``REEL_AUTOMATION_YOUTUBE_ACCESS_TOKEN``). Anything unconfigured
fails loudly with a clear `CompositionError`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.scheduler.closed_loop import StopSignal
from app.scheduler.schedule import IntervalSchedule
from app.services.composition import build_closed_loop

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """The production clock: tz-aware UTC (the injected `Now` seam, ADR 0054)."""
    return datetime.now(UTC)


def _install_stop_handlers(stop: StopSignal) -> None:
    """Wire SIGINT/SIGTERM to request a graceful shutdown of the loop.

    The handler only *sets* the cooperative `StopSignal` (and wakes the loop's
    inter-tick wait); the loop finishes its in-flight tick and exits cleanly —
    no work is abandoned mid-flight. Falls back silently if the platform/loop
    cannot register a signal handler (e.g. a non-main thread).
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.stop)
        except (NotImplementedError, RuntimeError):
            logger.warning("could not install handler for %s; rely on KeyboardInterrupt", sig)


async def _run(*, once: bool) -> None:
    # Construct Settings explicitly (not the cached module-level instance) so the
    # CLI reads the current environment / .env at invocation time, mirroring
    # app.cli.make_video.
    settings = Settings()
    bundle = build_closed_loop(settings)
    try:
        if once:
            report = await bundle.runner.run_once()
            logger.info("single tick complete: %s", report)
            return
        stop = StopSignal()
        _install_stop_handlers(stop)
        schedule = IntervalSchedule(
            interval=timedelta(seconds=settings.loop_interval_seconds),
            anchor=_now(),
        )
        logger.info("closed loop starting in %s mode", settings.loop_mode)
        await bundle.runner.run_forever(schedule, now=_now, sleep=asyncio.sleep, stop=stop)
        logger.info("closed loop stopped gracefully")
    finally:
        # Close the providers' httpx clients before the loop exits (ADR 0044).
        for closable in bundle.closables:
            await closable.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    once = "--once" in sys.argv[1:]
    asyncio.run(_run(once=once))


if __name__ == "__main__":
    main()
