"""Tests for the ``app.cli.doctor`` preflight (hermetic — no network, no exec).

The doctor re-implements the composition root's wiring conditions to produce a
full ✓/✗ table. These tests prove each branch reads the right key, that the exit
code gates on hard failures, and — via an anti-drift pin — that a fully-satisfied
`Settings` both passes the doctor *and* lets `build_research_deps` construct
(construction is offline, per the composition tests' own note), so the doctor
cannot silently diverge from the real wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.cli import doctor
from app.core.config import Settings
from app.services.composition import build_research_deps


def _settings(**overrides: object) -> Settings:
    """A fully-satisfied `Settings` (every hard requirement met) plus overrides.

    Mirrors ``tests/services/test_composition.py``'s helper so a developer's real
    ``.env`` cannot leak into the assertions. The defaults make every doctor row
    that depends on config pass; tests override one field to exercise a ✗.
    """
    base: dict[str, object] = {
        "default_provider": "openai-compatible",
        "base_url": "https://api.example.com/v1",
        "api_key": SecretStr("sk-test"),
        "search_provider": "tavily",
        "search_api_key": SecretStr("tvly-test"),
        "stock_api_key": SecretStr("pexels-test"),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _binaries_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: pretend ffmpeg/ffprobe are on PATH so config rows are isolated."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture(autouse=True)
def _kokoro_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (kokoro backend): pretend the package + model files are present.

    The doctor's kokoro check is offline — `find_spec` (no import executed) plus a
    file stat. The default `_settings()` uses the relative ``kokoro-v1.0.onnx`` /
    ``voices-v1.0.bin`` paths, which don't exist in the test cwd, so both signals
    are mocked here; individual tests override one to exercise a ✗.
    """
    monkeypatch.setattr(
        doctor.importlib.util, "find_spec", lambda name: object() if "kokoro" in name else None
    )
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: True)


@pytest.fixture(autouse=True)
def _isolate_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in a clean temp cwd so the default ``renders`` output dir and
    the ``.env`` probe resolve under ``tmp_path`` — no repo-tree pollution and no
    interference from a developer's real ``.env``.
    """
    monkeypatch.chdir(tmp_path)


def _row(results: list[doctor.CheckResult], prefix: str) -> doctor.CheckResult:
    return next(r for r in results if r.label.startswith(prefix))


# --- Model provider branches -------------------------------------------------


def test_openai_compatible_all_set_passes() -> None:
    results = doctor.run_checks(_settings())
    assert _row(results, "LLM provider").ok


def test_openai_compatible_missing_base_url_fails() -> None:
    row = _row(doctor.run_checks(_settings(base_url="")), "LLM provider")
    assert not row.ok
    assert "REEL_AUTOMATION_BASE_URL" in row.hint


def test_openai_compatible_missing_api_key_fails() -> None:
    row = _row(doctor.run_checks(_settings(api_key=SecretStr(""))), "LLM provider")
    assert not row.ok
    assert "REEL_AUTOMATION_API_KEY" in row.hint


def test_gemini_missing_key_fails() -> None:
    row = _row(doctor.run_checks(_settings(default_provider="gemini")), "LLM provider")
    assert not row.ok
    assert "REEL_AUTOMATION_GEMINI_API_KEY" in row.hint


def test_registry_preset_reads_its_own_key_not_base_url() -> None:
    # groq must read groq_api_key — base_url being set must NOT satisfy it.
    missing = _row(doctor.run_checks(_settings(default_provider="groq")), "LLM provider")
    assert not missing.ok
    assert "REEL_AUTOMATION_GROQ_API_KEY" in missing.hint

    present = _row(
        doctor.run_checks(_settings(default_provider="groq", groq_api_key=SecretStr("gsk-x"))),
        "LLM provider",
    )
    assert present.ok


def test_ollama_needs_no_key() -> None:
    # Ollama is keyless (requires_key=False) — must pass with no key set.
    row = _row(doctor.run_checks(_settings(default_provider="ollama")), "LLM provider")
    assert row.ok


def test_unknown_provider_fails() -> None:
    row = _row(doctor.run_checks(_settings(default_provider="bogus")), "LLM provider")
    assert not row.ok
    assert "REEL_AUTOMATION_DEFAULT_PROVIDER" in row.hint


# --- Search, TTS, stock branches ---------------------------------------------


def test_search_tavily_missing_key_fails() -> None:
    row = _row(doctor.run_checks(_settings(search_api_key=SecretStr(""))), "Search provider")
    assert not row.ok
    assert "REEL_AUTOMATION_SEARCH_API_KEY" in row.hint


def test_search_brave_reads_brave_key() -> None:
    missing = _row(doctor.run_checks(_settings(search_provider="brave")), "Search provider")
    assert not missing.ok
    assert "REEL_AUTOMATION_BRAVE_API_KEY" in missing.hint

    present = _row(
        doctor.run_checks(_settings(search_provider="brave", brave_api_key=SecretStr("b-x"))),
        "Search provider",
    )
    assert present.ok


def test_search_unknown_fails() -> None:
    row = _row(doctor.run_checks(_settings(search_provider="bogus")), "Search provider")
    assert not row.ok
    assert "REEL_AUTOMATION_SEARCH_PROVIDER" in row.hint


def test_tts_kokoro_default_passes_when_package_and_files_present() -> None:
    # The autouse fixtures mock find_spec + file presence: the default kokoro
    # backend is green with no TTS service key (the keystone, doctor-side).
    row = _row(doctor.run_checks(_settings()), "TTS backend")
    assert row.ok


def test_tts_kokoro_missing_package_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    row = _row(doctor.run_checks(_settings()), "TTS backend")
    assert not row.ok
    assert "pip install kokoro-onnx" in row.hint


def test_tts_kokoro_missing_model_files_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: False)
    row = _row(doctor.run_checks(_settings()), "TTS backend")
    assert not row.ok
    assert "kokoro-v1.0.onnx" in row.hint and "voices-v1.0.bin" in row.hint


def test_tts_nvidia_backend_reads_its_key() -> None:
    missing = _row(doctor.run_checks(_settings(tts_backend="nvidia")), "TTS backend")
    assert not missing.ok
    assert "REEL_AUTOMATION_NVIDIA_TTS_API_KEY" in missing.hint

    present = _row(
        doctor.run_checks(_settings(tts_backend="nvidia", nvidia_tts_api_key=SecretStr("nv-x"))),
        "TTS backend",
    )
    assert present.ok


def test_tts_huggingface_backend_reads_its_key() -> None:
    missing = _row(doctor.run_checks(_settings(tts_backend="huggingface")), "TTS backend")
    assert not missing.ok
    assert "REEL_AUTOMATION_HUGGINGFACE_TTS_API_KEY" in missing.hint


def test_tts_unknown_backend_fails() -> None:
    row = _row(doctor.run_checks(_settings(tts_backend="bogus")), "TTS backend")
    assert not row.ok
    assert "REEL_AUTOMATION_TTS_BACKEND" in row.hint


def test_stock_missing_key_fails() -> None:
    row = _row(doctor.run_checks(_settings(stock_api_key=SecretStr(""))), "Stock B-roll key")
    assert not row.ok
    assert "REEL_AUTOMATION_STOCK_API_KEY" in row.hint


# --- Binaries (mock shutil.which both ways) ----------------------------------


def test_ffmpeg_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    results = doctor.run_checks(_settings())
    ffmpeg = _row(results, "ffmpeg")
    ffprobe = _row(results, "ffprobe")
    assert not ffmpeg.ok and not ffprobe.ok
    assert "brew install ffmpeg" in ffmpeg.hint


def test_only_ffprobe_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor.shutil, "which", lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg"
    )
    results = doctor.run_checks(_settings())
    assert _row(results, "ffmpeg").ok
    assert not _row(results, "ffprobe").ok


# --- Output dir --------------------------------------------------------------


def test_output_dir_created_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "renders"
    row = _row(doctor.run_checks(_settings(media_output_dir=str(target))), "Output dir")
    assert row.ok
    assert target.is_dir()


# --- .env soft row -----------------------------------------------------------


def test_env_file_row_is_soft() -> None:
    # Even if absent, the .env row must never gate the exit code (hard=False).
    env_row = _row(doctor.run_checks(_settings()), ".env file")
    assert env_row.hard is False


# --- Exit code gating --------------------------------------------------------


def test_main_exits_zero_when_all_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor, "Settings", lambda: _settings())
    doctor.main()  # no SystemExit raised → exit 0
    out = capsys.readouterr().out
    assert "READY" in out


def test_main_exits_nonzero_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "Settings", lambda: _settings(api_key=SecretStr("")))
    with pytest.raises(SystemExit) as exc:
        doctor.main()
    assert exc.value.code == 1


def test_main_exits_nonzero_on_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "Settings", lambda: _settings())
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        doctor.main()
    assert exc.value.code == 1


# --- Anti-drift pin against the real composition root ------------------------


def test_satisfied_settings_pass_doctor_and_build_research_deps() -> None:
    """A fully-satisfied `Settings` must both go all-green (config rows) and let
    the real composition root construct — so the doctor cannot drift from the
    conditions `composition.py` actually enforces. Construction is offline (no
    network until the first call), per the composition tests' own note.
    """
    settings = _settings()
    results = doctor.run_checks(settings)
    config_rows = [r for r in results if not r.label.startswith(("ffmpeg", "ffprobe"))]
    assert all(r.ok for r in config_rows if r.hard)

    # The real research wiring succeeds on the same settings.
    build_research_deps(settings)
