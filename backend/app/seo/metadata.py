"""Deterministic SEO metadata builder — `CreatorPacket` + `Report` → `VideoMetadata`.

A pure, stdlib-only *tool* (CLAUDE.md §4 — no judgment, no LLM, no I/O): it
projects already-synthesized creative material into the title / description /
tags / hashtags that drive YouTube Shorts discovery (CLAUDE.md §3.4 publishing
support). Because every output is a deterministic function of the inputs, the
whole derivation is equality-testable with no model present.

Design (mirrors the ffmpeg adapter's pure/impure split, ADR 0023, applied to a
value derivation rather than a rendered file):

* `VideoMetadata` is a **value DTO** — no minted id, no timestamp. Metadata is a
  derived value, not a produced artifact with its own lifecycle, so it follows
  the `KeyFact`/`HookIdea` "no id at v1" sub-unit precedent rather than the
  `RenderedVideo` artifact pattern. It still carries a `produced_via`
  provenance string (``"seo:deterministic"``), symmetric with the media DTOs.
* `MetadataBuilder.build` is pure: same inputs → identical `VideoMetadata`.

§11 (evidence vs inference) carried to the discovery surface: the headline
(title) and the description's lead hook are sourced **only** from cleanly-grounded
creative material — a hook resting on a disputed/contradicted finding is never
promoted to the title (the repo-wide "don't amplify unverified claims in polished
outputs" ethos; see ADR 0039). Disputed key facts still appear in the description
body (full transparency), just never as the headline.

LLM-polished copy (a model rewriting the title/description for punch) is a
documented **future enhancement**, layered *over* this deterministic floor the
same way `report.py` layers model prose over code-derived citations/caveats — it
would never author the tags/hashtags or relax the title-length invariant.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.research_state import (
    CreatorPacket,
    KeyFact,
    Report,
    SupportLevel,
)

_STRICT = ConfigDict(extra="forbid")

PROVIDER_NAME = "seo:deterministic"

# --- YouTube discovery limits (verified 2026-06; see ADR 0039) --------------
# Hard platform invariants, not preferences: a title over 100 chars is rejected
# by the upload API, and YouTube ignores *all* hashtags on a video once there
# are more than 15 (a hard cutoff, not a gradual penalty). Only the first three
# hashtags render above the title. We stay well under the cap (3-5 is the
# discovery sweet spot) so attribution is never silently dropped.
MAX_TITLE_LEN = 100
MAX_HASHTAGS = 5
MAX_TAGS = 15

# A small, documented stopword set for tag extraction. Stdlib-only by design
# (CLAUDE.md: `pyproject.toml` is off-limits, so no NLP dependency) — this is a
# deliberate floor, not a linguistics engine.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)

# A "word" for tag/hashtag extraction: alphanumerics, kept simple and unicode-
# unaware on purpose (the deterministic floor; richer tokenization is the future
# LLM-polish enhancement's concern).
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


class MetadataError(RuntimeError):
    """Raised when metadata cannot be built from the given packet + report.

    The single failure type for the builder (symmetric with the media layer's
    `CompositionError` / `ThumbnailError`): a packet/report mismatch or an input
    with no usable headline source surfaces here, never a silent wrong output.
    """


class VideoMetadata(BaseModel):
    """Discovery-oriented upload metadata for a short-form video.

    A value DTO (no id / no timestamp — see the module docstring): the
    deterministic projection of a `CreatorPacket` + `Report` into the fields an
    upload needs. ``title`` is guaranteed ``<= 100`` chars (a hard YouTube
    invariant the builder enforces, never merely hopes). ``hashtags`` are bare
    (no leading ``#``) and capped so YouTube never drops all of them; the caller
    composes the final description (``description`` already inlines a leading
    hashtag line for the first few).
    """

    model_config = _STRICT

    title: str = Field(max_length=MAX_TITLE_LEN)
    description: str
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    produced_via: str = PROVIDER_NAME


def _truncate_on_word_boundary(text: str, limit: int) -> str:
    """Truncate ``text`` to at most ``limit`` chars, preferring a word boundary.

    Deterministic and pure. If the text already fits it is returned unchanged
    (stripped). Otherwise it is cut to ``limit`` and rolled back to the last
    whitespace so a word is never split mid-token; an ellipsis character ``…``
    (1 char, so the result stays ``<= limit``) marks the truncation. A single
    over-long word with no whitespace is hard-cut with the ellipsis.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    # Reserve one char for the ellipsis.
    head = text[: limit - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return f"{cut.rstrip()}…"


def _is_safe_headline(fact_or_none: KeyFact | None) -> bool:
    """A key fact is headline-safe iff it is not disputed.

    §11 guard for the headline surface: a *contradicted* fact is never promoted to
    the title. A single-source fact **may** still headline — it is surfaced
    honestly in the description body's "(note: single source)" marker, and this
    matches the hook path (the primary title source), which likewise blocks only
    disputed findings; tightening one path but not the other would let a
    single-source *hook* headline while a single-source *fact* could not. Blocking
    disputed-only is the consistent design (see ADR 0039 §3).
    """
    return fact_or_none is not None and not fact_or_none.disputed


class MetadataBuilder:
    """Builds `VideoMetadata` from a `CreatorPacket` and its source `Report`.

    Stateless and pure: `build` is a deterministic function of its inputs. The
    two inputs are both required because the discovery fields are split across
    them — the *headline material* (hooks, key facts) lives on the packet, while
    the *sources* (citation urls) live only on the report. The builder asserts
    the packet was built from the given report (``packet.report_id ==
    report.id``) and raises `MetadataError` otherwise, surfacing a wiring bug
    rather than silently mixing artifacts from two jobs.
    """

    name = PROVIDER_NAME

    def build(self, *, packet: CreatorPacket, report: Report) -> VideoMetadata:
        """Project ``packet`` + ``report`` into discovery-ready `VideoMetadata`.

        Title: the first headline-safe hook (head = highest priority, per the
        `CreatorPacket` ordering convention), else a headline-safe key fact, else
        the report title — then word-boundary-truncated to ``<= 100`` chars.
        Description: lead hook → key points (key-fact statements) → sources
        (deduped citation urls) → a trailing hashtag line (first few hashtags).
        Tags/hashtags: stopword-filtered keywords from the title, section
        headings, and key-fact statements.
        """
        if packet.report_id != report.id:
            raise MetadataError(
                "packet/report mismatch: packet.report_id="
                f"{packet.report_id!r} but report.id={report.id!r}"
            )

        title = self._build_title(packet=packet, report=report)
        tags = self._build_tags(title=title, report=report, packet=packet)
        hashtags = tags[:MAX_HASHTAGS]
        description = self._build_description(packet=packet, report=report, hashtags=hashtags)

        return VideoMetadata(
            title=title,
            description=description,
            tags=tags,
            hashtags=hashtags,
        )

    def _build_title(self, *, packet: CreatorPacket, report: Report) -> str:
        """Choose and length-bound the headline. Always returns ``<= 100`` chars."""
        # 1. First hook (head-priority); hooks are model prose without grounding
        #    flags, but we only promote one whose referenced findings are not
        #    disputed (resolved below via the key-fact grounding map).
        disputed_finding_ids = {f.finding_id for f in packet.key_facts if f.disputed}
        for hook in packet.hooks:
            text = hook.text.strip()
            if not text:
                continue
            if any(fid in disputed_finding_ids for fid in hook.finding_ids):
                continue  # §11: never headline a hook resting on a disputed finding
            return _truncate_on_word_boundary(text, MAX_TITLE_LEN)

        # 2. A headline-safe key fact.
        for fact in packet.key_facts:
            if _is_safe_headline(fact) and fact.statement.strip():
                return _truncate_on_word_boundary(fact.statement, MAX_TITLE_LEN)

        # 3. The report title is the always-present floor.
        if report.title.strip():
            return _truncate_on_word_boundary(report.title, MAX_TITLE_LEN)

        raise MetadataError("no usable headline source (empty hooks, key facts, and report title)")

    def _build_description(
        self, *, packet: CreatorPacket, report: Report, hashtags: list[str]
    ) -> str:
        """Compose the description: hook → key points → sources → hashtag line."""
        blocks: list[str] = []

        # Lead with the first hook (only the first ~150 chars show before "…more",
        # so the hook earns the prime real estate); fall back to the abstract.
        lead = next((h.text.strip() for h in packet.hooks if h.text.strip()), "")
        if not lead:
            lead = report.abstract.strip()
        if lead:
            blocks.append(lead)

        # Key points: each key-fact statement, with a transparency marker on any
        # disputed/weakly-supported fact (§11 — surfaced in the body, never buried).
        key_points = [
            self._key_point_line(fact) for fact in packet.key_facts if fact.statement.strip()
        ]
        if key_points:
            blocks.append("Key points:\n" + "\n".join(key_points))

        # Sources: deduped citation urls, in first-seen order (provenance carried
        # to the discovery surface, the §11 non-omittability one layer out).
        seen: set[str] = set()
        source_urls: list[str] = []
        for citation in report.citations:
            if citation.source_url not in seen:
                seen.add(citation.source_url)
                source_urls.append(citation.source_url)
        if source_urls:
            blocks.append("Sources:\n" + "\n".join(source_urls))

        # Trailing hashtag line: the first few hashtags (the ones that render
        # above the title). Bare tags get the leading '#'.
        if hashtags:
            blocks.append(" ".join(f"#{tag}" for tag in hashtags))

        return "\n\n".join(blocks)

    @staticmethod
    def _key_point_line(fact: KeyFact) -> str:
        """A single key-point bullet, marking disputed/weak grounding (§11).

        Uses the same disputed-then-weak ordering as the M11/M12
        `finding_caveat_kind` predicate (a `KeyFact` carries the identical
        ``disputed`` / ``weakest_support`` flags), so the SEO surface never drifts
        from the report's caveats / packet's warnings on what counts as unsafe.
        Inlined rather than calling that `Finding`-typed predicate to avoid a
        cross-shape adapter.
        """
        statement = fact.statement.strip()
        if fact.disputed:
            return f"- {statement} (note: sources disagree)"
        if fact.weakest_support is SupportLevel.SINGLE_SOURCE:
            return f"- {statement} (note: single source)"
        return f"- {statement}"

    def _build_tags(self, *, title: str, report: Report, packet: CreatorPacket) -> list[str]:
        """Extract deterministic, deduped keyword tags (stdlib-only).

        Drawn from the title, section headings, and key-fact statements — the
        most topical text available. Tokens are lowercased, stopword-filtered,
        deduped (first-seen order), require >= 3 chars, and capped at
        ``MAX_TAGS`` so the hashtag slice never trips YouTube's >15 cutoff.
        """
        corpus: list[str] = [title]
        corpus += [section.heading for section in report.sections]
        corpus += [fact.statement for fact in packet.key_facts]

        tags: list[str] = []
        seen: set[str] = set()
        for text in corpus:
            for match in _WORD_RE.findall(text):
                token = match.lower()
                if len(token) < 3 or token in _STOPWORDS or token in seen:
                    continue
                seen.add(token)
                tags.append(token)
                if len(tags) >= MAX_TAGS:
                    return tags
        return tags
