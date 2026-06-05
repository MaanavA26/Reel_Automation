"""Typed `ChannelProfile` — the per-channel brand/config object for Reel Automation.

A `ChannelProfile` is the durable, on-brand configuration for a single faceless
short-form channel: its niche and topic seeds, target platform(s), TTS voice,
narrative tone/persona, posting cadence, banned topics, and public branding. It
is the config the downstream steps read to keep a channel consistent across runs:

- **topic sourcing** reads ``niche`` / ``topic_seeds`` (what to research) and
  ``banned_topics`` (what to avoid),
- **scripting** reads ``tone`` / ``persona`` (how the narration should sound),
- **TTS** reads ``tts_voice_id`` (which voice to synthesize — the media-layer
  `TTSProvider.synthesize(*, text, voice)` `voice` argument),
- **SEO / publishing** reads ``platforms`` and ``branding`` (handle, hashtags).

This is the project's first concrete slice of the future *style / brand memory*
layer (CLAUDE.md §3.4). Per CLAUDE.md §4 the profile and its store are
deterministic **config / tool** state, not an agent: they hold the brand
contract; the reasoning agents *read* it. All models are strict
(`extra='forbid'`) with id-prefixed opaque ids, mirroring the repo's scheme
(`app.schemas.research_state`, `app.media.schemas`).

`_gen_id` is intentionally a small local copy of the `research_state` /
`media.schemas` helper: the channels package is decoupled from those layers (it
imports nothing from `app.schemas` or `app.media`), the same decoupling the
Media layer documents (ADR 0019). The scheme is kept in sync by the shared,
documented convention (prefix + 64 bits hex), not by a cross-layer import of a
private symbol. See ADR 0042.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # 64 bits of entropy via secrets.token_hex(8); hex-only suffix keeps the
    # underscore prefix-delimiter unambiguous. Same scheme as ADR 0001's
    # `research_state._gen_id`, copied (not imported) to keep the layer
    # decoupled — see this module's docstring and ADR 0042.
    return f"{prefix}_{secrets.token_hex(8)}"


class Platform(StrEnum):
    """A short-form publishing target a channel posts to.

    Controlled vocabulary (CLAUDE.md §6 preference for policy/enum over free
    strings) so the SEO/publishing step can branch on a known set rather than
    parse arbitrary platform names.
    """

    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"


class NarrativeTone(StrEnum):
    """The narrative voice/register the scripting step should write in.

    A small controlled vocabulary the scripting agent reads as a style hint;
    finer persona nuance lives in the free-text `ChannelProfile.persona`.
    """

    AUTHORITATIVE = "authoritative"
    CASUAL = "casual"
    ENERGETIC = "energetic"
    EDUCATIONAL = "educational"
    INSPIRATIONAL = "inspirational"


class PostingCadence(StrEnum):
    """How often the channel publishes — a coarse scheduling hint.

    Deliberately a controlled vocabulary rather than a structured cron/interval:
    at v1 the consumer (a future scheduling/operations step) only needs a coarse
    rhythm. A precise schedule model is a documented follow-up (see ADR 0042).
    """

    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class Branding(BaseModel):
    """Public-facing brand identity for a channel (handle + default hashtags).

    Read by the SEO/publishing step. ``handle`` is the channel's public handle
    (e.g. ``"@reelautomation"``); ``hashtags`` is the default tag set appended to
    posts. Kept a distinct sub-model (not flattened onto `ChannelProfile`) so the
    branding contract can grow (logo uri, color, banner) without widening the
    profile's top-level surface.
    """

    model_config = _STRICT

    handle: str
    hashtags: list[str] = Field(default_factory=list)


class ChannelProfile(BaseModel):
    """The on-brand configuration for a single faceless short-form channel.

    One instance per channel the operator runs. Mutable by design (the
    `ChannelStore` updates a profile via return-new-state `model_copy`, mirroring
    `JobStore`); the store — not the schema — owns the ``updated_at`` bump.

    Required fields are the ones a downstream step cannot proceed without:
    ``name`` (addressable identity), ``niche`` (what to research), and
    ``tts_voice_id`` (the media layer's required `voice`). ``platforms`` must be
    non-empty — a channel with no publishing target is not a runnable channel.
    The remaining fields carry brand nuance with sensible defaults.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("chan"))
    name: str
    niche: str
    topic_seeds: list[str] = Field(default_factory=list)
    platforms: list[Platform] = Field(min_length=1)
    tts_voice_id: str
    tone: NarrativeTone = NarrativeTone.EDUCATIONAL
    persona: str | None = None
    posting_cadence: PostingCadence = PostingCadence.WEEKLY
    banned_topics: list[str] = Field(default_factory=list)
    branding: Branding
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
