"""Named registry of preset OpenAI-compatible backends (CLAUDE.md §6).

The single :class:`~app.services.llm.openai_compatible.OpenAICompatibleProvider`
adapter (ADR 0007) speaks any backend exposing the OpenAI ``/chat/completions``
API — it is selected entirely by ``base_url`` + ``api_key`` + ``model``. This
module turns that into an *operator-friendly* control surface: instead of pasting
a raw URL, an operator names a known backend (``"groq"``, ``"nvidia"``,
``"huggingface"``, ``"ollama"``) and the registry supplies the matching
``base_url``; the operator supplies only the key + model (per-role models stay
policy-routed — see :mod:`app.services.llm.policy`).

**Why this exists alongside ``factory.py``.** The composition root
(:func:`app.services.llm.factory.build_router_from_settings`) wires the *generic*
default slot — one ``Settings.base_url`` + ``Settings.api_key`` pointing at
whatever backend the operator typed by hand. This registry is the complementary
*by-name* path: it knows each preset's URL so the operator need not, and it lets
several providers' keys coexist in one ``.env`` (``groq_api_key``,
``nvidia_api_key``, ``huggingface_api_key``) so switching backend is a name
change, not a URL+key edit. Both build the same adapter class; neither owns
routing policy.

Base URLs are documented presets, overridable only by editing this registry
(they change rarely). Per ADR 0007's precedent for the Groq *model* id, confirm a
preset against the provider's live docs if a request 404s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr

from app.core.config import Settings
from app.services.llm.openai_compatible import OpenAICompatibleProvider


@dataclass(frozen=True)
class ProviderPreset:
    """A known OpenAI-compatible backend: its ``base_url`` + how to read its key.

    ``api_key`` is a callable over :class:`Settings` (not a stringly-typed attr
    name) so the wiring is type-checked and refactor-safe. ``requires_key`` is
    ``False`` for local backends (Ollama) where any non-empty string is accepted
    by the upstream API; such presets fall back to a placeholder so the adapter's
    nonempty-``base_url`` invariant is the only hard requirement.
    """

    base_url: str
    api_key: Callable[[Settings], SecretStr]
    requires_key: bool = True


# A non-empty placeholder for keyless local backends (e.g. Ollama), which accept
# any bearer token. Keeps the adapter call uniform without a special case.
_LOCAL_PLACEHOLDER_KEY = SecretStr("ollama")


PROVIDER_REGISTRY: dict[str, ProviderPreset] = {
    # Groq — fast free tier; base_url confirmed in backend/.env.example.
    "groq": ProviderPreset(
        base_url="https://api.groq.com/openai/v1",
        api_key=lambda s: s.groq_api_key,
    ),
    # NVIDIA build / NIM — OpenAI-compatible hosted endpoint.
    "nvidia": ProviderPreset(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=lambda s: s.nvidia_api_key,
    ),
    # HuggingFace Inference Providers router (OpenAI-compatible).
    "huggingface": ProviderPreset(
        base_url="https://router.huggingface.co/v1",
        api_key=lambda s: s.huggingface_api_key,
    ),
    # Local Ollama — no real key required (any bearer token is accepted).
    "ollama": ProviderPreset(
        base_url="http://localhost:11434/v1",
        api_key=lambda _s: _LOCAL_PLACEHOLDER_KEY,
        requires_key=False,
    ),
}


class UnknownProviderPresetError(ValueError):
    """Raised when ``build_provider`` is given a name not in the registry."""


class MissingProviderKeyError(ValueError):
    """Raised when a key-requiring preset is selected without its API key set."""


def build_provider(
    name: str, settings: Settings, *, use_schema_format: bool = False
) -> OpenAICompatibleProvider:
    """Build a configured ``OpenAICompatibleProvider`` for a named preset.

    Looks the preset's ``base_url`` up by ``name`` and reads its key from
    ``settings``; the model id is *not* passed here — it stays role-routed via the
    policy and supplied per call (``complete_structured(model=...)``).

    Fails loud at build time (mirroring ``factory._build_provider`` and the
    adapter's empty-``base_url`` check) so a missing key surfaces as a clear
    config error here rather than an opaque 401 at call time.

    Raises:
        UnknownProviderPresetError: if ``name`` is not a registered preset.
        MissingProviderKeyError: if a key-requiring preset has no key configured.
    """
    preset = PROVIDER_REGISTRY.get(name)
    if preset is None:
        known = ", ".join(sorted(PROVIDER_REGISTRY))
        raise UnknownProviderPresetError(
            f"unknown provider preset {name!r}; known presets are: {known}"
        )
    api_key = preset.api_key(settings).get_secret_value()
    if preset.requires_key and not api_key:
        raise MissingProviderKeyError(
            f"provider preset {name!r} requires an API key; set the corresponding "
            f"REEL_AUTOMATION_*_API_KEY (see backend/.env.example)"
        )
    return OpenAICompatibleProvider(
        base_url=preset.base_url, api_key=api_key, use_schema_format=use_schema_format
    )
