"""Channel / brand profiles — per-channel config for running faceless channels.

The first concrete slice of the future *style / brand memory* layer (CLAUDE.md
§3.4). A `ChannelProfile` is the on-brand config (niche, voice, persona, cadence,
branding) the topic-sourcing, scripting, TTS, and SEO steps read to keep a
channel consistent. Per CLAUDE.md §4 this package is a deterministic
**config / tool** layer, not an agent: it holds the brand contract; the reasoning
agents *read* it.

Public surface:

- `ChannelProfile` + its value types (`Platform`, `NarrativeTone`,
  `PostingCadence`, `Branding`) — the typed config object (`schemas`).
- `ChannelStore` (the CRUD `Protocol` seam) and `InMemoryChannelStore` (the
  process-local production default) — `store`. A durable backend is a documented
  follow-up that implements the same `Protocol` (ADR 0042).
- `FakeChannelStore` — the pre-seedable, call-recording test double (`fakes`).
"""

from app.channels.fakes import FakeChannelStore
from app.channels.schemas import (
    Branding,
    ChannelProfile,
    NarrativeTone,
    Platform,
    PostingCadence,
)
from app.channels.store import (
    ChannelNotFoundError,
    ChannelStore,
    ChannelStoreError,
    DuplicateChannelError,
    InMemoryChannelStore,
    InvalidUpdateError,
)

__all__ = [
    "Branding",
    "ChannelNotFoundError",
    "ChannelProfile",
    "ChannelStore",
    "ChannelStoreError",
    "DuplicateChannelError",
    "FakeChannelStore",
    "InMemoryChannelStore",
    "InvalidUpdateError",
    "NarrativeTone",
    "Platform",
    "PostingCadence",
]
