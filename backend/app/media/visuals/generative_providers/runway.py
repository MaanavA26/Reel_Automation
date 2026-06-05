"""Runway (Gen-3/Gen-4) `GenerativeVisualProvider` adapter (documented contract).

Speaks Runway's developer API text-to-video lifecycle:

1. **Submit** ``POST {base}/v1/text_to_video`` with ``Authorization: Bearer {key}``
   and a required ``X-Runway-Version`` date header, body
   ``{"model", "promptText", "ratio", "duration"}``. The response carries the
   task ``id``.
2. **Poll** ``GET {base}/v1/tasks/{id}`` until ``status`` is terminal. Status
   values: ``PENDING`` / ``THROTTLED`` / ``RUNNING`` (non-terminal),
   ``SUCCEEDED`` (done), ``FAILED`` / ``CANCELLED`` (failed). On success the
   finished asset URL is the first entry of the ``output`` array.

Wire shape is **documented-contract, not live-validated** (no live call in this
offline sandbox; the NVIDIA-TTS / YouTube last-mile caveat — ADR 0047/0033/0053).
The shape is isolated in the hook methods so the first live call can confirm or
adjust it with a small edit, not a rewrite. Auth is a static bearer key passed at
construction (never read from global ``Settings``, never logged).
"""

from __future__ import annotations

from typing import Any

from app.media.visuals.generative import (
    GenerativeVisualError,
    JobState,
    PollOutcome,
    _PollingGenerativeProvider,
)

PROVIDER_NAME = "runway"
_DEFAULT_BASE_URL = "https://api.dev.runwayml.com"
#: Runway requires a dated API-version header on every request; this is the
#: documented stable value and is overridable at construction.
_DEFAULT_API_VERSION = "2024-11-06"
#: Default text-to-video model slug; overridable at construction.
_DEFAULT_MODEL = "gen4_turbo"
_ERR_BODY_MAX = 500

#: Runway accepts pixel-pair ``ratio`` strings, not "9:16" forms.
_RATIO_FOR_ASPECT: dict[str, str] = {
    "9:16": "720:1280",
    "16:9": "1280:720",
    "1:1": "960:960",
    "3:4": "1104:832",
    "4:3": "832:1104",
}

_TERMINAL_DONE = "SUCCEEDED"
_TERMINAL_FAILED = frozenset({"FAILED", "CANCELLED"})


class RunwayGenerativeProvider(_PollingGenerativeProvider):
    """A `GenerativeVisualProvider` over the Runway developer text-to-video API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        api_version: str = _DEFAULT_API_VERSION,
        **kwargs: Any,
    ) -> None:
        if not api_key:
            raise GenerativeVisualError("api_key is required (Runway bearer key)")
        super().__init__(**kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_version = api_version

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Runway-Version": self._api_version,
        }

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        ratio = _RATIO_FOR_ASPECT.get(aspect)
        if ratio is None:
            raise GenerativeVisualError(
                f"runway: unsupported aspect {aspect!r} (known: {sorted(_RATIO_FOR_ASPECT)})"
            )
        body = {
            "model": self._model,
            "promptText": prompt,
            "ratio": ratio,
            # Runway's documented durations are whole seconds (5 or 10); round up
            # so a sub-second request still yields a valid clip length.
            "duration": max(1, round(duration_ms / 1000)),
        }
        return f"{self._base_url}/v1/text_to_video", body

    def _parse_submit(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"runway: unexpected submit response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        task_id = data.get("id")
        if not task_id or not isinstance(task_id, str):
            raise GenerativeVisualError(
                f"runway: submit response missing task 'id': {repr(data)[:_ERR_BODY_MAX]}"
            )
        return task_id

    def _build_poll(self, job_id: str) -> tuple[str, str]:
        return "GET", f"{self._base_url}/v1/tasks/{job_id}"

    def _parse_poll(self, data: Any) -> PollOutcome:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"runway: unexpected task response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        status = data.get("status")
        if status == _TERMINAL_DONE:
            output = data.get("output")
            uri = output[0] if isinstance(output, list) and output else None
            uri = uri if isinstance(uri, str) else None
            return PollOutcome(state=JobState.DONE, result_uri=uri)
        if status in _TERMINAL_FAILED:
            failure = data.get("failure") or data.get("failureCode")
            return PollOutcome(state=JobState.FAILED, error=str(failure) if failure else status)
        return PollOutcome(state=JobState.PENDING)
