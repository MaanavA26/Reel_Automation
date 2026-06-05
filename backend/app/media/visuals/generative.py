"""Provider-neutral contract for AI *generative* video (prompt -> a new clip).

The existing `VisualProvider` (`base.py`) is a *retrieval* seam: a keyword maps to
an *already-existing* stock clip via a single synchronous request. Generative
video is a different shape entirely — a *prompt* is turned into a **newly
synthesized** clip via an **async job**: submit a request, get a job id, poll
until the job reaches a terminal state, then read the finished asset's URL. The
request schema, auth, poll lifecycle, and result location differ per vendor
(Veo / Runway / Luma / Pika / Kling) with no universal API shape — so this is a
*new* protocol, not an extension of `VisualProvider.search` (ADR 0053).

Per CLAUDE.md §3.3/§4 this is deterministic *tool/service* work, exactly like
`StockVisualProvider`: the upstream Short-Form Content Strategist decides *what*
to depict; the adapter *executes* the generation and is the only thing that mints
a real asset ``uri`` (an LLM never authors one), keeping the
retrieval/evidence-vs-inference boundary structural (CLAUDE.md §11).

The output is the **same** `VisualClip` descriptor the retrieval seam produces,
so the composition step is unchanged: an adapter returns a hosted result *URL*
as ``uri`` (these vendors deliver large finished assets as download URLs, never
inline bytes), which the composition root's existing `_make_filesystem_visual_sink`
fetches to a local ``file://`` uri for ffmpeg. Provenance is ``produced_via``
like ``"genvideo:veo"`` (the ``genvideo:`` prefix distinguishes a synthesized
clip from a retrieved one, ``"visuals:stock"``).

Unlike `StockVisualProvider`, which *reads* dimensions from the vendor response,
a generative adapter *requests* a resolution from the chosen aspect ratio and
sets ``width``/``height`` from that request (the poll response rarely carries
dimensions). `_dims_for_aspect` is the shared, pure mapping.

This module ships the protocol + DTO helpers + the `_PollingGenerativeProvider`
template-method base that owns the submit->poll->fetch loop once (per-vendor
adapters implement only the wire-shape hooks; see `generative_providers/`) + a
hermetic `FakeGenerativeVisualProvider`. The concrete adapters speak each
vendor's *documented* contract; **none is live-validated** in this offline
sandbox (the last-mile caveat ADR 0047/0033 already carry).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.lifecycle import CloseOwnedClientMixin
from app.media.visuals.base import VisualClip, VisualKind

#: Default target aspect — the system makes vertical short-form (9:16).
DEFAULT_ASPECT = "9:16"
#: Default requested clip length when the caller does not specify one.
DEFAULT_DURATION_MS = 5_000

#: Portrait/landscape/square resolutions per documented aspect ratio. The
#: generated clip's dimensions come from the *request* (the poll response rarely
#: reports them), so this map is the single source of truth. 9:16 targets a
#: 1080x1920 vertical frame — the short-form default the composition layer wants.
_ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "21:9": (2560, 1080),
    "9:21": (1080, 2560),
}


def _dims_for_aspect(aspect: str) -> tuple[int, int]:
    """Map an aspect-ratio string to ``(width, height)``. Pure.

    Raises `GenerativeVisualError` for an unsupported aspect rather than guessing
    — a wrong frame size silently corrupts composition (CLAUDE.md §11: fail loud,
    no silent defaults).
    """
    dims = _ASPECT_DIMS.get(aspect)
    if dims is None:
        raise GenerativeVisualError(
            f"unsupported aspect {aspect!r} (known: {sorted(_ASPECT_DIMS)})"
        )
    return dims


class GenerativeVisualError(RuntimeError):
    """Raised on a contract-violating response, a vendor-side job failure, or a poll timeout.

    The error boundary mirrors the rest of the media/search layer (ADR 0007/0021/0047):
    *transport* failures (429 rate-limit, timeout, 5xx via ``raise_for_status``)
    propagate as native ``httpx`` errors for the caller/Orchestrator to handle
    (retries/budgets live there). This *domain* error covers what is specific to
    generative video: an unparseable response shape, a job that the vendor reports
    as ``failed``, and exhausting the poll budget before the job finishes. A
    failed job must never look like a transport error.
    """


class JobState(StrEnum):
    """The provider-neutral terminal/non-terminal state of a generation job.

    Each adapter maps its vendor's own status vocabulary
    (e.g. Runway ``RUNNING`` / Kling ``processing`` / Luma ``dreaming``) onto
    these three, so the polling loop in `_PollingGenerativeProvider` stays
    vendor-agnostic.
    """

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class PollOutcome:
    """The result of parsing one poll response.

    ``state`` drives the loop; ``result_uri`` is the finished asset URL (set only
    when ``state`` is `JobState.DONE`); ``error`` is the vendor's failure reason
    (set only when ``state`` is `JobState.FAILED`), surfaced in the raised
    `GenerativeVisualError`.
    """

    state: JobState
    result_uri: str | None = None
    error: str | None = None


@runtime_checkable
class GenerativeVisualProvider(Protocol):
    """A backend that *synthesizes* a video clip from a text prompt via an async job.

    Implementations submit a generation request, poll the vendor's job endpoint
    until it terminates, and return a `VisualClip` whose ``uri`` is the finished
    asset's hosted URL (the bytes are streamed by the vendor/composition step; the
    layer traffics in descriptors). Async — generation is slow network I/O.
    """

    name: str

    async def generate(
        self,
        *,
        prompt: str,
        duration_ms: int = DEFAULT_DURATION_MS,
        aspect: str = DEFAULT_ASPECT,
    ) -> VisualClip: ...


class _PollingGenerativeProvider(CloseOwnedClientMixin):
    """Template-method base owning the submit -> poll -> fetch loop once.

    All five target vendors share one lifecycle (submit a request, receive a job
    id, poll until a terminal state, map the result URL); only the *wire shapes*
    differ. This base owns the loop, the error boundary, and the bounded-budget
    polling; each concrete adapter implements only the per-vendor hooks below.

    Auth is **not** unified here: `_auth_headers` is a hook because the vendors
    diverge sharply (Veo uses a GCP access token + project/region in the URL;
    Kling mints a short-lived HS256 JWT from key+secret; Runway/Luma/Pika are
    static bearer keys). Baking one ``Authorization: Bearer`` into the base would
    force a refactor for Veo and Kling.

    Two distinct timeouts, deliberately not conflated: the per-request httpx
    ``timeout`` is small (each poll is a fast status check); the **wall-clock poll
    budget** (``poll_attempts`` x ``poll_interval_s``) is *minutes*, because
    generation itself is slow. ``sleep`` is injected so hermetic tests run
    instantly and the timeout path is testable.
    """

    name: str = "generative"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        poll_interval_s: float = 5.0,
        poll_attempts: int = 120,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if poll_attempts < 1:
            raise GenerativeVisualError("poll_attempts must be >= 1")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._poll_interval_s = poll_interval_s
        self._poll_attempts = poll_attempts
        self._sleep = sleep

    # --- per-vendor hooks ---------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return the auth headers for every request. Per-vendor (see class doc)."""
        raise NotImplementedError

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        """Return ``(submit_url, json_body)`` for the generation request. Pure."""
        raise NotImplementedError

    def _parse_submit(self, data: Any) -> str:
        """Extract the job id from the submit response, or raise `GenerativeVisualError`."""
        raise NotImplementedError

    def _build_poll(self, job_id: str) -> tuple[str, str]:
        """Return ``(http_method, poll_url)`` for a single status poll. Pure."""
        raise NotImplementedError

    def _poll_body(self, job_id: str) -> dict[str, Any] | None:
        """JSON body for the poll request (Veo's ``:fetchPredictOperation`` POST), else None."""
        return None

    def _parse_poll(self, data: Any) -> PollOutcome:
        """Map a poll response onto a `PollOutcome`, or raise on an unparseable shape."""
        raise NotImplementedError

    # --- shared lifecycle ---------------------------------------------------

    async def generate(
        self,
        *,
        prompt: str,
        duration_ms: int = DEFAULT_DURATION_MS,
        aspect: str = DEFAULT_ASPECT,
    ) -> VisualClip:
        """Submit a generation, poll to completion, and map the result to a `VisualClip`.

        ``width``/``height`` come from the requested ``aspect`` (validated up
        front), not the response. Raises `GenerativeVisualError` on a job the
        vendor reports as ``failed`` or on exhausting the poll budget; transport
        errors propagate as ``httpx`` exceptions.
        """
        width, height = _dims_for_aspect(aspect)

        submit_url, body = self._build_submit(prompt=prompt, duration_ms=duration_ms, aspect=aspect)
        submit_resp = await self._client.post(submit_url, headers=self._auth_headers(), json=body)
        submit_resp.raise_for_status()
        job_id = self._parse_submit(submit_resp.json())

        result_uri = await self._poll_until_done(job_id)

        return VisualClip(
            uri=result_uri,
            kind=VisualKind.VIDEO,
            width=width,
            height=height,
            duration_ms=duration_ms,
            produced_via=f"genvideo:{self.name}",
        )

    async def _poll_until_done(self, job_id: str) -> str:
        """Poll the job until terminal; return the result URL or raise on failure/timeout."""
        method, poll_url = self._build_poll(job_id)
        body = self._poll_body(job_id)
        for _ in range(self._poll_attempts):
            response = await self._client.request(
                method, poll_url, headers=self._auth_headers(), json=body
            )
            response.raise_for_status()
            outcome = self._parse_poll(response.json())
            if outcome.state is JobState.DONE:
                if not outcome.result_uri:
                    raise GenerativeVisualError(
                        f"{self.name}: job {job_id} done but carried no result uri"
                    )
                return outcome.result_uri
            if outcome.state is JobState.FAILED:
                raise GenerativeVisualError(
                    f"{self.name}: job {job_id} failed: {outcome.error or 'unknown reason'}"
                )
            await self._sleep(self._poll_interval_s)
        raise GenerativeVisualError(
            f"{self.name}: job {job_id} did not finish within "
            f"{self._poll_attempts} polls ({self._poll_attempts * self._poll_interval_s}s budget)"
        )


@dataclass
class RecordedGeneration:
    """A single `generate` invocation captured by the fake."""

    prompt: str
    duration_ms: int
    aspect: str


class FakeGenerativeVisualProvider:
    """A hermetic `GenerativeVisualProvider` for offline tests (no network, no bytes).

    Returns a deterministic `VisualClip` with a synthetic ``uri`` derived from the
    call count and dimensions from the requested aspect, and records each call for
    assertions. Mirrors `FakeVisualProvider` / `FakeTTSProvider` (testing-standards:
    "don't mock what you can fake").
    """

    name = "fake"

    def __init__(self, clips: Sequence[VisualClip] | None = None) -> None:
        self._clips: list[VisualClip] = list(clips or [])
        self.calls: list[RecordedGeneration] = []

    async def generate(
        self,
        *,
        prompt: str,
        duration_ms: int = DEFAULT_DURATION_MS,
        aspect: str = DEFAULT_ASPECT,
    ) -> VisualClip:
        self.calls.append(RecordedGeneration(prompt=prompt, duration_ms=duration_ms, aspect=aspect))
        if self._clips:
            return self._clips[(len(self.calls) - 1) % len(self._clips)]
        width, height = _dims_for_aspect(aspect)
        return VisualClip(
            uri=f"fake://genvideo/{len(self.calls)}.mp4",
            kind=VisualKind.VIDEO,
            width=width,
            height=height,
            duration_ms=duration_ms,
            produced_via=f"genvideo:{self.name}",
        )
