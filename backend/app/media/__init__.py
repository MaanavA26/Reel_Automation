"""Media Production layer (CLAUDE.md §3.3).

Provider-neutral *tool/service* seams (CLAUDE.md §4 — media work is
deterministic execution, never agents) for the media-making pipeline: TTS,
subtitle generation, and composition/FFmpeg assembly. This package is a
**bounded scaffold** (CLAUDE.md §7/§13): it establishes the interfaces, typed
artifact DTOs, hermetic fakes, and the one piece of real deterministic logic
(SRT/VTT formatting). Concrete adapters (ElevenLabs, Veo, real ffmpeg) and the
Deep Research creator-packet → media handoff contract are deferred to a future
milestone — see `docs/adrs/0019-media-production-layer.md`.

The layer is intentionally decoupled from the Deep Research schema: it imports
nothing from `app.schemas` / `app.agents` / `app.workflows`, so it can be built,
tested, and showcased on its own.
"""
