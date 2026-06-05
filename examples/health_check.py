"""Example: probe the API health endpoint.

Run (from ``backend/`` so ``app`` is importable, with a server running)::

    python ../examples/health_check.py
"""

from __future__ import annotations

from _common import resolve_base_url

from app.client import ReelAutomationClient


def main() -> None:
    with ReelAutomationClient(resolve_base_url()) as client:
        health = client.health()
    print(f"service={health.service!r} status={health.status!r}")


if __name__ == "__main__":
    main()
