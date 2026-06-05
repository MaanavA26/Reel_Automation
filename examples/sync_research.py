"""Example: submit a research job synchronously and print its terminal state.

`submit_research` blocks until the server finishes the full Deep Research
workflow, then returns the terminal `ResearchState`. The client's generous
default timeout accommodates the server-side run.

Run (from ``backend/`` so ``app`` is importable, with a server running)::

    python ../examples/sync_research.py "the cognition of octopuses"
"""

from __future__ import annotations

import sys

from _common import resolve_base_url

from app.client import ReelAutomationAPIError, ReelAutomationClient

DEFAULT_TOPIC = "the cognition of octopuses"


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOPIC

    with ReelAutomationClient(resolve_base_url()) as client:
        try:
            state = client.submit_research(topic)
        except ReelAutomationAPIError as exc:
            # Default deployment has no production adapter wired -> 503.
            print(f"request failed ({exc.status_code}): {exc.detail}")
            return

    print(f"job {state.id} finished with status={state.status.value}")
    if state.error:
        print(f"  error: {state.error}")
    print(f"  sub-questions planned: {len(state.plan.sub_questions)}")
    print(f"  sources acquired:      {len(state.acquisition.sources)}")
    print(f"  evidence extracted:    {len(state.acquisition.evidence)}")
    print(f"  findings synthesized:  {len(state.reasoning.synthesis.findings)}")
    print(f"  reports published:     {len(state.publishing.reports)}")
    print(f"  creator packets:       {len(state.publishing.packets)}")


if __name__ == "__main__":
    main()
