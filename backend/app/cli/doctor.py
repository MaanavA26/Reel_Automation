"""Preflight readiness check for a live ``make video`` run (offline-safe).

``python -m app.cli.doctor`` prints a clear ✓/✗ table of everything an
end-to-end video render needs, **without making any paid/network calls**. It is
the "is my machine ready?" companion to `app.cli.make_video`: where the pipeline
fails loud *after* spending an LLM/search/TTS call to discover a missing key,
the doctor surfaces every gap up front, naming the exact ``REEL_AUTOMATION_*``
variable (or system binary) to set.

Design (CLAUDE.md §4 — this is a deterministic *tool*, not an agent):

* The checks **re-implement** the same conditions `app.services.composition`
  applies at wiring time, rather than calling ``build_research_deps`` /
  ``build_media_deps``. Those builders fail fast (the model raises before search
  is ever evaluated) and ``build_media_deps`` mutates the filesystem — neither
  can produce a *full* table where every row is reported independently. Each
  check here is a small pure function returning a `CheckResult`, so one run
  surfaces all problems at once. A drift test pins these against the real
  composition root.
* `Settings` is constructed fresh (not the cached `get_settings()`), mirroring
  `app.cli.make_video`, so the doctor reads the current environment / ``.env``.

One deliberate deviation from `composition.py`: the **stock-media key is treated
as a hard requirement** here. The composition root tolerates a missing stock key
(``visuals=None``), but the live ffmpeg render then fails because it needs ≥1
visual — so for the doctor's "ready for `make video`" purpose, a missing stock
key is a hard ✗, not a silent pass.

Exit code: ``1`` if any hard requirement is missing, else ``0``.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.services.llm.gemini import GeminiProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.providers import PROVIDER_REGISTRY
from app.services.search.brave_search import BraveSearchProvider
from app.services.search.live import TavilySearchProvider


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one readiness check: a labelled, pass/fail row with a hint.

    ``hint`` is the actionable remediation shown when ``ok`` is ``False`` (the
    exact env var to set, or the binary to install). ``hard`` rows gate the exit
    code; a soft row (e.g. the ``.env`` file presence) is informational only.
    """

    label: str
    ok: bool
    hint: str = ""
    hard: bool = True


# --- Individual checks (each mirrors a condition in composition.py) ----------


#: The dotenv path `Settings` loads from (mirrors `config.py`'s ``env_file``).
_ENV_FILE = ".env"


def _check_env_file(_settings: Settings) -> CheckResult:
    """Soft: is a local ``.env`` present (relative to the working directory)?

    Informational only — `Settings` reads exported environment variables too, so
    a missing ``.env`` is not a hard failure (the per-variable rows below carry
    the real readiness signal). Hard-failing here would be a false negative for
    anyone configuring via exported env vars.
    """
    env_file = Path(_ENV_FILE)
    return CheckResult(
        label=f".env file present ({env_file})",
        ok=env_file.is_file(),
        hint="cp .env.example .env  (then paste your keys) — or export REEL_AUTOMATION_* vars",
        hard=False,
    )


def _check_model_provider(settings: Settings) -> CheckResult:
    """Hard: is the configured LLM provider's required key/base_url set?

    Mirrors `composition._build_model_provider` branch-for-branch: each
    ``default_provider`` reads a *different* key. Ollama requires no key
    (``requires_key=False`` in the registry), so it always passes.
    """
    name = settings.default_provider
    label = f"LLM provider ({name})"

    if name == OpenAICompatibleProvider.name:
        if not settings.base_url:
            return CheckResult(label, False, "set REEL_AUTOMATION_BASE_URL")
        if not settings.api_key.get_secret_value():
            return CheckResult(label, False, "set REEL_AUTOMATION_API_KEY")
        return CheckResult(label, True)

    if name == GeminiProvider.name:
        if not settings.gemini_api_key.get_secret_value():
            return CheckResult(label, False, "set REEL_AUTOMATION_GEMINI_API_KEY")
        return CheckResult(label, True)

    preset = PROVIDER_REGISTRY.get(name)
    if preset is not None:
        if preset.requires_key and not preset.api_key(settings).get_secret_value():
            return CheckResult(label, False, f"set REEL_AUTOMATION_{name.upper()}_API_KEY")
        return CheckResult(label, True)

    known = ", ".join(
        sorted({OpenAICompatibleProvider.name, GeminiProvider.name, *PROVIDER_REGISTRY})
    )
    return CheckResult(
        label,
        False,
        f"set REEL_AUTOMATION_DEFAULT_PROVIDER to one of: {known}",
    )


def _check_search_provider(settings: Settings) -> CheckResult:
    """Hard: is the configured search provider's key set?

    Mirrors `composition._build_search_provider`: ``tavily`` reads
    ``search_api_key``, ``brave`` reads ``brave_api_key``.
    """
    name = settings.search_provider
    label = f"Search provider ({name})"

    if name == TavilySearchProvider.name:
        if not settings.search_api_key.get_secret_value():
            return CheckResult(label, False, "set REEL_AUTOMATION_SEARCH_API_KEY")
        return CheckResult(label, True)

    if name == BraveSearchProvider.name:
        if not settings.brave_api_key.get_secret_value():
            return CheckResult(label, False, "set REEL_AUTOMATION_BRAVE_API_KEY")
        return CheckResult(label, True)

    known = ", ".join(sorted({TavilySearchProvider.name, BraveSearchProvider.name}))
    return CheckResult(
        label, False, f"set REEL_AUTOMATION_SEARCH_PROVIDER to one of: {known}"
    )


def _check_tts(settings: Settings) -> CheckResult:
    """Hard: is TTS configured (base_url + key)?

    Mirrors the two `composition.build_media_deps` guards for the live render.
    """
    label = "TTS endpoint"
    if not settings.tts_base_url:
        return CheckResult(label, False, "set REEL_AUTOMATION_TTS_BASE_URL")
    if not settings.tts_api_key.get_secret_value():
        return CheckResult(label, False, "set REEL_AUTOMATION_TTS_API_KEY")
    return CheckResult(label, True)


def _check_stock(settings: Settings) -> CheckResult:
    """Hard (doctor-specific): is a stock B-roll key set?

    `composition.build_media_deps` treats this as optional (``visuals=None``),
    but the live ffmpeg render then fails because it needs ≥1 visual. For the
    doctor's "ready for `make video`" purpose this is a hard requirement.
    """
    label = "Stock B-roll key"
    if not settings.stock_api_key.get_secret_value():
        return CheckResult(
            label,
            False,
            "set REEL_AUTOMATION_STOCK_API_KEY (ffmpeg needs >= 1 visual to render)",
        )
    return CheckResult(label, True)


def _check_binary(name: str) -> CheckResult:
    """Hard: is ``name`` on PATH (offline-safe — `shutil.which` does no exec)?"""
    found = shutil.which(name)
    return CheckResult(
        label=f"{name} on PATH",
        ok=found is not None,
        hint=f"install ffmpeg (provides {name}): brew install ffmpeg",
    )


def _check_output_dir(settings: Settings) -> CheckResult:
    """Hard: does the render output dir exist or can it be created?

    Reports the resolved absolute path. If the directory is absent, attempts to
    create it (the same ``mkdir(parents=True, exist_ok=True)`` the live media
    build does), so a clean checkout passes without a manual step.
    """
    output_dir = Path(settings.media_output_dir).resolve()
    label = f"Output dir ({output_dir})"
    if output_dir.is_dir():
        return CheckResult(label, True)
    if output_dir.exists():
        return CheckResult(
            label, False, f"{output_dir} exists but is not a directory — move it aside"
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(label, False, f"could not create {output_dir}: {exc}")
    return CheckResult(label, True)


def run_checks(settings: Settings) -> list[CheckResult]:
    """Run every readiness check against ``settings`` and return the full table.

    Every check runs regardless of earlier failures, so one invocation surfaces
    all gaps at once (unlike the fail-fast composition builders).
    """
    checks: list[Callable[[Settings], CheckResult]] = [
        _check_env_file,
        _check_model_provider,
        _check_search_provider,
        _check_tts,
        _check_stock,
        _check_output_dir,
    ]
    results = [check(settings) for check in checks]
    results.append(_check_binary("ffmpeg"))
    results.append(_check_binary("ffprobe"))
    return results


# --- Rendering + entry point -------------------------------------------------


def _format_table(results: list[CheckResult]) -> str:
    """Render the results as an aligned ✓/✗ table with remediation hints."""
    width = max((len(r.label) for r in results), default=0)
    lines = ["Reel Automation — preflight (make video readiness)", ""]
    for r in results:
        mark = "✓" if r.ok else "✗"
        line = f"  {mark}  {r.label.ljust(width)}"
        if not r.ok and r.hint:
            line += f"   → {r.hint}"
        lines.append(line)
    lines.append("")
    failed_hard = [r for r in results if not r.ok and r.hard]
    if failed_hard:
        lines.append(f"NOT READY: {len(failed_hard)} required check(s) failed (see → above).")
    else:
        lines.append('READY: all required checks passed. Run: make video TOPIC="<your topic>"')
    return "\n".join(lines)


def main() -> None:
    """Print the readiness table and exit non-zero if any hard check failed."""
    settings = Settings()
    results = run_checks(settings)
    print(_format_table(results))
    if any(not r.ok and r.hard for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
