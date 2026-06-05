"""Luma Dream Machine (Ray) `GenerativeVisualProvider` adapter (documented contract).

Speaks Luma's Dream Machine API text-to-video lifecycle (the contract published in
Luma's official OpenAPI spec, ``github.com/lumalabs/lumaai-api``):

1. **Submit** ``POST {base}/dream-machine/v1/generations/video`` with
   ``Authorization: Bearer {key}``, body
   ``{"prompt", "model", "aspect_ratio", "resolution", "duration", "loop"}``.
   The response carries the generation ``id`` and ``state``.
2. **Poll** ``GET {base}/dream-machine/v1/generations/{id}`` until ``state`` is
   terminal: ``queued`` / ``dreaming`` (non-terminal), ``completed`` (done),
   ``failed`` (failed, with ``failure_reason``). The finished video URL is
   ``assets.video``.

Wire shape is **documented-contract, not live-validated** (no live call in this
offline sandbox; the NVIDIA-TTS / YouTube last-mile caveat — ADR 0047/0033/0053).
This is the cleanest of the five vendors (static bearer + native aspect strings +
single GET poll). The key is passed at construction and never logged.
"""

from __future__ import annotations

from typing import Any

from app.media.visuals.generative import (
    GenerativeVisualError,
    JobState,
    PollOutcome,
    _PollingGenerativeProvider,
)

PROVIDER_NAME = "luma"
_DEFAULT_BASE_URL = "https://api.lumalabs.ai"
_DEFAULT_MODEL = "ray-2"
_DEFAULT_RESOLUTION = "720p"
_ERR_BODY_MAX = 500

# Luma accepts the "9:16" aspect strings natively (its documented enum), so no
# remap is needed beyond the base seam's validation.
_NON_TERMINAL = frozenset({"queued", "dreaming"})


class LumaGenerativeProvider(_PollingGenerativeProvider):
    """A `GenerativeVisualProvider` over the Luma Dream Machine API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        resolution: str = _DEFAULT_RESOLUTION,
        **kwargs: Any,
    ) -> None:
        if not api_key:
            raise GenerativeVisualError("api_key is required (Luma bearer key)")
        super().__init__(**kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._resolution = resolution

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "prompt": prompt,
            "model": self._model,
            "aspect_ratio": aspect,
            "resolution": self._resolution,
            # Luma's documented durations are second-suffixed strings ("5s"/"9s").
            "duration": f"{max(1, round(duration_ms / 1000))}s",
            "loop": False,
        }
        # Per Luma's official OpenAPI spec the create path is ``/generations/video``
        # (the GET poll is ``/generations/{id}``), under the ``/dream-machine/v1``
        # server prefix.
        return f"{self._base_url}/dream-machine/v1/generations/video", body

    def _parse_submit(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"luma: unexpected submit response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        gen_id = data.get("id")
        if not gen_id or not isinstance(gen_id, str):
            raise GenerativeVisualError(
                f"luma: submit response missing generation 'id': {repr(data)[:_ERR_BODY_MAX]}"
            )
        return gen_id

    def _build_poll(self, job_id: str) -> tuple[str, str]:
        return "GET", f"{self._base_url}/dream-machine/v1/generations/{job_id}"

    def _parse_poll(self, data: Any) -> PollOutcome:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"luma: unexpected generation response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        state = data.get("state")
        if state == "completed":
            assets = data.get("assets")
            uri = assets.get("video") if isinstance(assets, dict) else None
            uri = uri if isinstance(uri, str) else None
            return PollOutcome(state=JobState.DONE, result_uri=uri)
        if state == "failed":
            return PollOutcome(state=JobState.FAILED, error=data.get("failure_reason"))
        if state in _NON_TERMINAL:
            return PollOutcome(state=JobState.PENDING)
        # An unknown state is treated as still-pending rather than a hard failure
        # (forward-compatible with new Luma intermediate states); the poll budget
        # still bounds the wait.
        return PollOutcome(state=JobState.PENDING)
