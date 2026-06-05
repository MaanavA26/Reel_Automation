"""Kling AI `GenerativeVisualProvider` adapter (documented contract).

Distinctive auth: Kling does not take a static key — every request carries a
short-lived **HS256 JWT** minted from an access key (``ak``) + secret key (``sk``),
with claims ``{"iss": ak, "exp": now+30m, "nbf": now-5s}``, sent as
``Authorization: Bearer {jwt}``. The JWT is minted **per request** (the seam's
`_auth_headers` hook is called for each call) so it never expires mid-poll.

Lifecycle:

1. **Submit** ``POST {base}/v1/videos/text2video`` body
   ``{"model_name", "prompt", "aspect_ratio", "duration"}``; response carries
   ``data.task_id``.
2. **Poll** ``GET {base}/v1/videos/text2video/{task_id}`` until ``data.task_status``
   is terminal: ``submitted`` / ``processing`` (non-terminal), ``succeed`` (done,
   video at ``data.task_result.videos[0].url``), ``failed`` (with
   ``data.task_status_msg``).

The JWT is signed with the Python **standard library** (``hmac`` + ``hashlib`` +
base64url) — no new dependency (the repo's no-new-dep posture, ADR 0047) — and the
signing is a pure, unit-testable function. Wire shape is **documented-contract,
not live-validated** (no live call in this offline sandbox; the NVIDIA-TTS /
YouTube last-mile caveat — ADR 0047/0033/0053). ``ak``/``sk`` are passed at
construction and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.media.visuals.generative import (
    GenerativeVisualError,
    JobState,
    PollOutcome,
    _PollingGenerativeProvider,
)

PROVIDER_NAME = "kling"
_DEFAULT_BASE_URL = "https://api-beijing.klingai.com"
_DEFAULT_MODEL = "kling-v1"
#: JWT lifetime (Kling tokens expire after 30 minutes; mint with headroom).
_JWT_TTL_S = 1800
#: Backdate ``nbf`` slightly to tolerate minor clock skew (Kling's documented value).
_JWT_NBF_SKEW_S = 5
_ERR_BODY_MAX = 500

_NON_TERMINAL = frozenset({"submitted", "processing"})


def _b64url(raw: bytes) -> str:
    """Base64url-encode without padding (JWT segment encoding). Pure."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_jwt_hs256(*, access_key: str, secret_key: str, now: int, ttl_s: int = _JWT_TTL_S) -> str:
    """Mint a Kling HS256 JWT from ``ak``/``sk`` using only the stdlib. Pure.

    Builds ``{"alg": "HS256", "typ": "JWT"}`` . ``{"iss": ak, "exp": now+ttl,
    "nbf": now-skew}`` and HMAC-SHA256-signs it with ``sk``. Deterministic given
    ``now`` (so the integration/unit boundary is testable with a fixed clock).
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": now + ttl_s, "nbf": now - _JWT_NBF_SKEW_S}
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(
        secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


class KlingGenerativeProvider(_PollingGenerativeProvider):
    """A `GenerativeVisualProvider` over the Kling AI text2video API (JWT auth)."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        **kwargs: Any,
    ) -> None:
        if not access_key or not secret_key:
            raise GenerativeVisualError("access_key and secret_key are required (Kling ak/sk)")
        super().__init__(**kwargs)
        self._access_key = access_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    def _auth_headers(self) -> dict[str, str]:
        # Mint a fresh JWT per request so it never expires mid-poll.
        token = encode_jwt_hs256(
            access_key=self._access_key,
            secret_key=self._secret_key,
            now=int(time.time()),
        )
        return {"Authorization": f"Bearer {token}"}

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "model_name": self._model,
            "prompt": prompt,
            "aspect_ratio": aspect,
            # Kling's documented durations are second-strings ("5" / "10").
            "duration": str(max(1, round(duration_ms / 1000))),
        }
        return f"{self._base_url}/v1/videos/text2video", body

    def _parse_submit(self, data: Any) -> str:
        payload = _data_envelope(data)
        task_id = payload.get("task_id")
        if not task_id or not isinstance(task_id, str):
            raise GenerativeVisualError(
                f"kling: submit response missing data.task_id: {repr(data)[:_ERR_BODY_MAX]}"
            )
        return task_id

    def _build_poll(self, job_id: str) -> tuple[str, str]:
        return "GET", f"{self._base_url}/v1/videos/text2video/{job_id}"

    def _parse_poll(self, data: Any) -> PollOutcome:
        payload = _data_envelope(data)
        status = payload.get("task_status")
        if status == "succeed":
            result = payload.get("task_result")
            videos = result.get("videos") if isinstance(result, dict) else None
            uri = None
            if isinstance(videos, list) and videos and isinstance(videos[0], dict):
                uri = videos[0].get("url")
            uri = uri if isinstance(uri, str) else None
            return PollOutcome(state=JobState.DONE, result_uri=uri)
        if status == "failed":
            return PollOutcome(state=JobState.FAILED, error=payload.get("task_status_msg"))
        if status in _NON_TERMINAL:
            return PollOutcome(state=JobState.PENDING)
        return PollOutcome(state=JobState.PENDING)


def _data_envelope(data: Any) -> dict[str, Any]:
    """Return the ``data`` object from a Kling envelope, or raise on a bad shape.

    Kling wraps every response as ``{"code", "message", "data": {...}}``; the
    fields this adapter reads all live under ``data``.
    """
    if not isinstance(data, dict):
        raise GenerativeVisualError(f"kling: unexpected response: {repr(data)[:_ERR_BODY_MAX]}")
    envelope = data.get("data")
    if not isinstance(envelope, dict):
        raise GenerativeVisualError(
            f"kling: response missing 'data' object: {repr(data)[:_ERR_BODY_MAX]}"
        )
    return envelope
