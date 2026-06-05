"""Turn a topic into a finished short-form video (the end-to-end CLI; ADR 0032).

This is the command-line surface of the `VideoPipeline` linchpin: it builds the
pipeline from environment configuration, runs ``topic → research → creator packet
→ media → finished video``, and prints the resulting `VideoArtifact` as JSON
(its ``video_uri`` points at the rendered file).

Usage (from the ``backend/`` directory)::

    cp .env.example .env          # set your LLM + search + TTS keys
    python -m app.cli.make_video "why fusion ignition is hard"

A live run needs: an LLM provider key, a search provider key, a TTS endpoint +
key, and the ``ffmpeg`` binary on PATH (and, for real B-roll, a stock-media key).
Anything unconfigured fails loudly with a clear `CompositionError`. Configuration
is read from environment variables (and a local ``.env``) with the
``REEL_AUTOMATION_`` prefix — see ``.env.example`` for the full set.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import Settings
from app.services.video import build_video_pipeline

_DEFAULT_TOPIC = "the James Webb Space Telescope"


async def _run(topic: str) -> None:
    # Construct Settings explicitly (not the cached module-level instance) so the
    # CLI reads the current environment / .env at invocation time, mirroring
    # app.cli.plan.
    pipeline = build_video_pipeline(Settings())
    artifact = await pipeline.create(topic)
    print(artifact.model_dump_json(indent=2))


def main() -> None:
    topic = " ".join(sys.argv[1:]).strip() or _DEFAULT_TOPIC
    asyncio.run(_run(topic))


if __name__ == "__main__":
    main()
