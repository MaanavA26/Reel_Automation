"""Pika `GenerativeVisualProvider` adapter via the fal.ai queue (documented contract).

Pika has **no stable first-party public REST API**; its self-serve programmatic
path is hosting on **fal.ai**, which exposes Pika models (e.g.
``fal-ai/pika/v2.2/text-to-video``) behind fal's generic asynchronous *queue*
API. This adapter therefore speaks the **fal queue** contract, parameterized by
the Pika model slug — so the same adapter serves any fal-hosted Pika version.

fal queue lifecycle (distinct from the others — the result lives at a *separate*
URL the submit hands back, not in the poll body):

1. **Submit** ``POST {base}/{model}`` with ``Authorization: Key {fal_key}``, body
   ``{"prompt", "aspect_ratio", "duration"}``. The response carries
   ``request_id``, ``status_url``, and ``response_url`` (full URLs).
2. **Poll** ``GET {status_url}`` until ``status`` is terminal:
   ``IN_QUEUE`` / ``IN_PROGRESS`` (non-terminal), ``COMPLETED`` (done),
   anything else treated as failed.
3. **Fetch result** ``GET {response_url}`` — the completed output object whose
   ``video.url`` is the finished clip.

The base seam's three-state poll loop is reused, but `generate` is overridden to
thread fal's submit-returned ``status_url``/``response_url`` (the base assumes the
poll URL is built from a job id; fal hands back full URLs and splits status from
result). Wire shape is **documented-contract, not live-validated** — and carries
the extra risk that the *Pika-specific* fal input schema (vs fal's generic queue
envelope, which is stable) may differ per model (no live call here; the NVIDIA-TTS
last-mile caveat — ADR 0047/0053). The fal key is passed at construction, never
logged. ``risk``: this is the second-highest-risk adapter after Veo (indirection
through a third party + a model-specific input schema).
"""

from __future__ import annotations

from typing import Any

from app.media.visuals.base import VisualClip, VisualKind
from app.media.visuals.generative import (
    DEFAULT_ASPECT,
    DEFAULT_DURATION_MS,
    GenerativeVisualError,
    JobState,
    PollOutcome,
    _dims_for_aspect,
    _PollingGenerativeProvider,
)

PROVIDER_NAME = "pika"
_DEFAULT_BASE_URL = "https://queue.fal.run"
#: Default fal-hosted Pika model slug; overridable at construction.
_DEFAULT_MODEL = "fal-ai/pika/v2.2/text-to-video"
_ERR_BODY_MAX = 500

_NON_TERMINAL = frozenset({"IN_QUEUE", "IN_PROGRESS"})


def _clip(data: Any) -> str:
    """Bounded repr of an upstream body for error messages (info-leak guard)."""
    return repr(data)[:_ERR_BODY_MAX]


class PikaGenerativeProvider(_PollingGenerativeProvider):
    """A `GenerativeVisualProvider` over fal.ai-hosted Pika (fal queue API)."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        **kwargs: Any,
    ) -> None:
        if not api_key:
            raise GenerativeVisualError("api_key is required (fal.ai key for Pika)")
        super().__init__(**kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    def _auth_headers(self) -> dict[str, str]:
        # fal authenticates with "Authorization: Key {fal_key}" (not Bearer).
        return {"Authorization": f"Key {self._api_key}"}

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "prompt": prompt,
            "aspect_ratio": aspect,
            "duration": max(1, round(duration_ms / 1000)),
        }
        return f"{self._base_url}/{self._model}", body

    async def generate(
        self,
        *,
        prompt: str,
        duration_ms: int = DEFAULT_DURATION_MS,
        aspect: str = DEFAULT_ASPECT,
    ) -> VisualClip:
        """Submit to the fal queue, poll ``status_url``, then fetch ``response_url``.

        Overrides the base loop because fal returns full ``status_url`` /
        ``response_url`` to thread (the base builds a poll URL from a job id, and
        the result lives at a separate URL rather than in the poll body).
        """
        width, height = _dims_for_aspect(aspect)
        submit_url, body = self._build_submit(prompt=prompt, duration_ms=duration_ms, aspect=aspect)
        submit_resp = await self._client.post(submit_url, headers=self._auth_headers(), json=body)
        submit_resp.raise_for_status()
        status_url, response_url = _parse_queue_submit(submit_resp.json())

        result_uri = await self._poll_status_and_fetch(status_url, response_url)
        return VisualClip(
            uri=result_uri,
            kind=VisualKind.VIDEO,
            width=width,
            height=height,
            duration_ms=duration_ms,
            produced_via=f"genvideo:{self.name}",
        )

    async def _poll_status_and_fetch(self, status_url: str, response_url: str) -> str:
        for _ in range(self._poll_attempts):
            status_resp = await self._client.get(status_url, headers=self._auth_headers())
            status_resp.raise_for_status()
            outcome = _parse_queue_status(status_resp.json())
            if outcome.state is JobState.DONE:
                result_resp = await self._client.get(response_url, headers=self._auth_headers())
                result_resp.raise_for_status()
                return _extract_video_url(result_resp.json())
            if outcome.state is JobState.FAILED:
                raise GenerativeVisualError(f"pika: fal job failed: {outcome.error or 'unknown'}")
            await self._sleep(self._poll_interval_s)
        raise GenerativeVisualError(
            f"pika: fal job did not finish within {self._poll_attempts} polls "
            f"({self._poll_attempts * self._poll_interval_s}s budget)"
        )

    # The base submit/poll-parse hooks are unused (generate is overridden); they
    # are defined to satisfy the abstract contract and never reached.
    def _parse_submit(self, data: Any) -> str:  # pragma: no cover - unused override
        raise NotImplementedError

    def _build_poll(self, job_id: str) -> tuple[str, str]:  # pragma: no cover - unused override
        raise NotImplementedError

    def _parse_poll(self, data: Any) -> PollOutcome:  # pragma: no cover - unused override
        raise NotImplementedError


def _parse_queue_submit(data: Any) -> tuple[str, str]:
    """Pull ``(status_url, response_url)`` from a fal submit response. Pure."""
    if not isinstance(data, dict):
        raise GenerativeVisualError(f"pika: unexpected submit response: {_clip(data)}")
    status_url = data.get("status_url")
    response_url = data.get("response_url")
    if not isinstance(status_url, str) or not isinstance(response_url, str):
        raise GenerativeVisualError(
            f"pika: submit response missing status_url/response_url: {repr(data)[:_ERR_BODY_MAX]}"
        )
    return status_url, response_url


def _parse_queue_status(data: Any) -> PollOutcome:
    """Map a fal queue status response onto a `PollOutcome`. Pure."""
    if not isinstance(data, dict):
        raise GenerativeVisualError(f"pika: unexpected status response: {_clip(data)}")
    status = data.get("status")
    if status == "COMPLETED":
        return PollOutcome(state=JobState.DONE)
    if status in _NON_TERMINAL:
        return PollOutcome(state=JobState.PENDING)
    return PollOutcome(state=JobState.FAILED, error=str(status))


def _extract_video_url(data: Any) -> str:
    """Pull ``video.url`` from a fal Pika result, or raise. Pure."""
    if not isinstance(data, dict):
        raise GenerativeVisualError(f"pika: unexpected result response: {_clip(data)}")
    video = data.get("video")
    uri = video.get("url") if isinstance(video, dict) else None
    if not isinstance(uri, str) or not uri:
        raise GenerativeVisualError(f"pika: result missing video.url: {repr(data)[:_ERR_BODY_MAX]}")
    return uri
