"""LLM-as-judge evaluation harness — "which model is best for role X" (CLAUDE.md §6).

A deterministic *service* (CLAUDE.md §4) that runs a task suite across explicit
``(provider, model)`` candidates, validates structured output, measures latency,
and scores quality with a pluggable `Judge` (rule-based default, optional
independent model judge). It productizes the methodology of
``docs/llm-model-selection.md`` (§3 candidate-by-task grid, §6 "make it
reproducible") into the reusable, offline-testable scaffold that doc calls for.

Public surface:

- `EvalTask` — one ``(system, prompt, schema)`` job, generic over its output schema.
- `EvalHarness` — runs candidates across tasks, scores, returns an `EvalReport`.
- `EvalResult` / `EvalReport` / `TaskRun` — typed, ranked outputs.
- `Judge` / `RuleBasedJudge` / `ModelJudge` / `QualityScore` — the pluggable scorer.
"""

from __future__ import annotations

from app.eval.harness import EvalConfigError, EvalHarness
from app.eval.judge import (
    Judge,
    JudgeError,
    ModelJudge,
    QualityScore,
    RuleBasedJudge,
)
from app.eval.report import EvalReport, EvalResult, TaskRun
from app.eval.task import EvalTask, ProviderRegistry

__all__ = [
    "EvalConfigError",
    "EvalHarness",
    "EvalReport",
    "EvalResult",
    "EvalTask",
    "Judge",
    "JudgeError",
    "ModelJudge",
    "ProviderRegistry",
    "QualityScore",
    "RuleBasedJudge",
    "TaskRun",
]
