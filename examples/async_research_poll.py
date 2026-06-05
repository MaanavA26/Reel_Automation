"""Example: enqueue a research job and poll it to completion.

Shows the async surface: `enqueue_job` returns immediately with a job id, then
`get_job` is polled until the `ResearchState.status` reaches a terminal value.

Run (from ``backend/`` so ``app`` is importable, with a server running)::

    python ../examples/async_research_poll.py "the cognition of octopuses"
"""

from __future__ import annotations

import sys
import time

from _common import resolve_base_url

from app.client import ReelAutomationAPIError, ReelAutomationClient
from app.schemas.research_state import JobStatus

DEFAULT_TOPIC = "the cognition of octopuses"
_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
_POLL_INTERVAL_SECONDS = 2.0
_MAX_POLLS = 150


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOPIC

    with ReelAutomationClient(resolve_base_url()) as client:
        try:
            job_id = client.enqueue_job(topic)
            print(f"enqueued job {job_id}; polling...")

            for _ in range(_MAX_POLLS):
                state = client.get_job(job_id)
                print(f"  status={state.status.value}")
                if state.status in _TERMINAL:
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            else:
                print("timed out waiting for the job to finish")
                return
        except ReelAutomationAPIError as exc:
            print(f"request failed ({exc.status_code}): {exc.detail}")
            return

    print(f"job {state.id} finished with status={state.status.value}")
    if state.error:
        print(f"  error: {state.error}")


if __name__ == "__main__":
    main()
