"""Typed schema for the human-review / approval gate (ADR 0051).

A `ReviewRecord` is one item awaiting a human sign-off between the automated
`safety/gate.py` and publish: a produced video lands here in `PENDING_REVIEW`, a
human lists pending items, then **approves** or **rejects** (with a reason). Only
an approved record proceeds to publish.

Contrast with `safety/verdict.py` (the *automated* gate's output): a
`SafetyVerdict` is deliberately **id-free, timestamp-free, and pure** — equal
inputs yield an equal verdict, because an automated policy check is a function of
its inputs. A `ReviewRecord` is the **opposite**: id'd, timestamped, and stateful,
because a human sign-off is an *event in time*, not a pure function — *who/when*
matters and must be recorded. See ADR 0051.

Per CLAUDE.md §11, the `reason` is the **authored judgment** (the human/caller
types it); code attaches every id, the status transition, and the
``decided_at``/``decided_via`` provenance — so a decision can never claim a
timestamp or transition it didn't actually make.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


def _gen_id(prefix: str) -> str:
    # A local copy of the `schemas/research_state` id scheme, keeping the review
    # package self-contained like the video package does (CLAUDE.md §11 / ADR
    # 0001): 64 bits of entropy, hex-only suffix so the ``_`` prefix-delimiter
    # stays unambiguous.
    return f"{prefix}_{secrets.token_hex(8)}"


class ReviewStatus(StrEnum):
    """Lifecycle state of a human-review item.

    Values lowercase to match `schemas.research_state.JobStatus` /
    `SafetyDecision`. ``PENDING_REVIEW`` is the entry state (awaiting a human);
    ``APPROVED`` / ``REJECTED`` are the terminal states a decision transitions to.
    """

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        """True iff this is a decided (non-pending) state."""
        return self is not ReviewStatus.PENDING_REVIEW


class ReviewRecord(BaseModel):
    """One produced item awaiting (or having received) a human sign-off.

    Strict + id-prefixed (``rev_``) like the repo's other DTOs. References the
    item under review by ``subject_id`` + a human-readable ``subject_label`` (for
    the pending list) rather than embedding the whole artifact — mirroring how
    `PublishCandidate` keeps ``packet_id`` for re-join.

    Provenance (CLAUDE.md §11): ``created_at`` is stamped at submit; on a decision,
    code attaches ``status``, ``decided_at``, and ``decided_via`` (the
    machine-readable "who/how", e.g. ``"review:api"``). ``reason`` is the
    human-authored judgment — optional on approve, required (and enforced
    non-blank) on reject by the service.
    """

    model_config = _STRICT

    id: str = Field(default_factory=lambda: _gen_id("rev"))
    subject_id: str = Field(
        description="Id of the produced item under review (e.g. a VideoArtifact id)."
    )
    subject_label: str = Field(
        default="",
        description="Human-readable label for the pending list (e.g. the narrative title).",
    )
    status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    reason: str | None = Field(
        default=None,
        description="Human-authored rationale for the decision (required on reject).",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decided_via: str | None = None
