"""Google Veo (Vertex AI) `GenerativeVisualProvider` adapter (documented contract).

The most divergent of the five vendors: Veo on Vertex AI uses a
*long-running-operation* lifecycle, a GCP **access token** (not a static API key),
and the project/region/model baked into the URL path.

1. **Submit** ``POST {base}/v1/projects/{project}/locations/{location}/publishers/
   google/models/{model}:predictLongRunning`` with ``Authorization: Bearer
   {access_token}``, body
   ``{"instances": [{"prompt": ...}], "parameters": {"aspectRatio", "durationSeconds",
   "sampleCount", "storageUri"}}``. The response carries the operation ``name``.
2. **Poll** ``POST {base}/.../{model}:fetchPredictOperation`` with body
   ``{"operationName": <name>}`` until ``done`` is ``true``. On success the
   finished video lives under ``response.videos[]`` as a ``gcsUri`` (when a
   ``storageUri`` was supplied) or inline ``bytesBase64Encoded``; an ``error``
   field signals a failed operation.

Two surfaces exist for Veo (this Vertex AI predict surface, and a separate
Gemini-API ``generativelanguage`` surface with API-key auth). This adapter
targets the **Vertex AI** surface — the documented production path — and is the
**highest-risk** of the five on the documented-vs-real contract (LRO shape, the
``storageUri`` requirement to get a URL rather than inline bytes, GCP token
refresh). Wire shape is **documented-contract, not live-validated** (no live call
in this offline sandbox; the NVIDIA-TTS last-mile caveat — ADR 0047/0053).

Auth: the GCP **access token** is passed at construction (the caller mints/refreshes
it via ADC / a service account — token refresh is the caller's responsibility, the
same deferral YouTube-publish made in ADR 0033). It is never read from ``Settings``
directly and never logged. A ``storage_uri`` (a ``gs://`` bucket prefix) is
required so Veo writes the result to GCS and returns a fetchable URI rather than
inline base64 (which the composition sink cannot fetch).
"""

from __future__ import annotations

from typing import Any

from app.media.visuals.generative import (
    GenerativeVisualError,
    JobState,
    PollOutcome,
    _PollingGenerativeProvider,
)

PROVIDER_NAME = "veo"
_DEFAULT_MODEL = "veo-3.0-generate-001"
_ERR_BODY_MAX = 500


class VeoGenerativeProvider(_PollingGenerativeProvider):
    """A `GenerativeVisualProvider` over the Vertex AI Veo predictLongRunning API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_token: str,
        project: str,
        location: str = "us-central1",
        storage_uri: str,
        model: str = _DEFAULT_MODEL,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not access_token:
            raise GenerativeVisualError("access_token is required (GCP OAuth bearer token)")
        if not project:
            raise GenerativeVisualError("project is required (GCP project id)")
        if not storage_uri:
            raise GenerativeVisualError(
                "storage_uri is required (a gs:// prefix so Veo returns a fetchable "
                "GCS uri rather than inline base64)"
            )
        super().__init__(**kwargs)
        self._access_token = access_token
        self._project = project
        self._location = location
        self._storage_uri = storage_uri
        self._model = model
        # The regional Vertex AI endpoint host derives from the location unless
        # an explicit base_url is injected (the test seam / a private endpoint).
        self._base_url = (base_url or f"https://{location}-aiplatform.googleapis.com").rstrip("/")

    def _model_path(self) -> str:
        return (
            f"{self._base_url}/v1/projects/{self._project}/locations/{self._location}"
            f"/publishers/google/models/{self._model}"
        )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _build_submit(
        self, *, prompt: str, duration_ms: int, aspect: str
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": aspect,
                "durationSeconds": max(1, round(duration_ms / 1000)),
                "sampleCount": 1,
                "storageUri": self._storage_uri,
            },
        }
        return f"{self._model_path()}:predictLongRunning", body

    def _parse_submit(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"veo: unexpected submit response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        op_name = data.get("name")
        if not op_name or not isinstance(op_name, str):
            raise GenerativeVisualError(
                f"veo: submit response missing operation 'name': {repr(data)[:_ERR_BODY_MAX]}"
            )
        return op_name

    def _build_poll(self, job_id: str) -> tuple[str, str]:
        # Veo polls via a POST to :fetchPredictOperation (not a GET on the op id).
        return "POST", f"{self._model_path()}:fetchPredictOperation"

    def _poll_body(self, job_id: str) -> dict[str, Any]:
        return {"operationName": job_id}

    def _parse_poll(self, data: Any) -> PollOutcome:
        if not isinstance(data, dict):
            raise GenerativeVisualError(
                f"veo: unexpected operation response: {repr(data)[:_ERR_BODY_MAX]}"
            )
        if data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            return PollOutcome(state=JobState.FAILED, error=message)
        if not data.get("done"):
            return PollOutcome(state=JobState.PENDING)
        uri = _extract_video_uri(data.get("response"))
        return PollOutcome(state=JobState.DONE, result_uri=uri)


def _extract_video_uri(response: Any) -> str | None:
    """Pull the first ``gcsUri`` from a Veo operation ``response.videos[]``.

    Only a GCS uri is usable downstream (the composition sink fetches a uri, not
    inline ``bytesBase64Encoded``); a response that carried only base64 yields
    ``None`` and the base seam raises a clear "done but no result uri" error.
    """
    if not isinstance(response, dict):
        return None
    videos = response.get("videos")
    if not isinstance(videos, list):
        return None
    for video in videos:
        if isinstance(video, dict):
            uri = video.get("gcsUri")
            if isinstance(uri, str) and uri:
                return uri
    return None
