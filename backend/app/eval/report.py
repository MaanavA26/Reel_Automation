"""Typed eval outputs — per-task runs, per-candidate aggregates, and the ranking.

Three layers, all `extra='forbid'` Pydantic DTOs (the repo's schema convention):

- `TaskRun` — one candidate on one task: did it produce schema-valid output, how
  long did it take, what quality did the judge assign (or why it errored).
- `EvalResult` — one candidate aggregated over the whole task suite: schema-pass
  *rate*, median latency, mean quality. The per-candidate verdict.
- `EvalReport` — every candidate plus a deterministic `ranking()` / `best()`, the
  reproducible answer to "which model is best for role X" (CLAUDE.md §6).

Ranking mirrors ``docs/llm-model-selection.md`` §1: schema-adherence is ranked
*first*, ahead of quality (a smarter model that flakes JSON is *worse* here), then
quality, then latency breaks remaining ties. The order is a total, lexicographic
key, so identical inputs rank identically — the reproducibility §6 asks for.
"""

from __future__ import annotations

from statistics import fmean, median

from pydantic import BaseModel, Field

from app.services.llm.router import ModelChoice


class TaskRun(BaseModel):
    """Outcome of running one candidate against one `EvalTask`."""

    model_config = {"extra": "forbid"}

    task_name: str
    schema_passed: bool = Field(description="Did the candidate return schema-valid output?")
    latency_s: float = Field(description="Wall-clock seconds for the candidate call.")
    quality: float | None = Field(
        default=None,
        description="Judge score, or None if schema failed / no judge applied.",
    )
    quality_rationale: str | None = None
    error: str | None = Field(
        default=None,
        description="Failure detail when schema_passed is False (or the judge erred).",
    )


class EvalResult(BaseModel):
    """One candidate's aggregate scorecard across the full task suite."""

    model_config = {"extra": "forbid"}

    provider: str
    model: str
    runs: list[TaskRun]

    @property
    def schema_pass_rate(self) -> float:
        """Fraction of tasks that yielded schema-valid output (0.0 to 1.0)."""
        if not self.runs:
            return 0.0
        return sum(1 for r in self.runs if r.schema_passed) / len(self.runs)

    @property
    def median_latency_s(self) -> float:
        """Median call latency across all tasks (p50, as in the reference doc)."""
        if not self.runs:
            return 0.0
        return median(r.latency_s for r in self.runs)

    @property
    def mean_quality(self) -> float | None:
        """Mean judge score over tasks that produced a quality score, else None."""
        scored = [r.quality for r in self.runs if r.quality is not None]
        if not scored:
            return None
        return fmean(scored)


# Total ordering for `best()`: higher schema-pass-rate wins; then higher mean
# quality (absent quality sorts last); then lower median latency. Returns a key
# usable with `sorted(..., reverse=True)` — bigger tuple = better candidate.
def _rank_key(result: EvalResult) -> tuple[float, float, float]:
    quality = result.mean_quality
    quality_key = quality if quality is not None else float("-inf")
    # Negate latency so "bigger is better" holds uniformly under reverse-sort.
    return (result.schema_pass_rate, quality_key, -result.median_latency_s)


class EvalReport(BaseModel):
    """The full eval: every candidate's scorecard plus a deterministic ranking."""

    model_config = {"extra": "forbid"}

    results: list[EvalResult]

    def ranking(self) -> list[EvalResult]:
        """Candidates best-first by (schema-pass-rate, quality, -latency).

        A stable sort over a total key: equal candidates keep input order, so the
        ranking is fully reproducible for the same inputs.
        """
        return sorted(self.results, key=_rank_key, reverse=True)

    def best(self) -> EvalResult:
        """The single best candidate. Raises `ValueError` on an empty report."""
        if not self.results:
            raise ValueError("cannot pick best from an empty EvalReport")
        return self.ranking()[0]

    def best_choice(self) -> ModelChoice:
        """The best candidate as a `ModelChoice`, ready to wire into a `RolePolicy`."""
        winner = self.best()
        return ModelChoice(provider=winner.provider, model=winner.model)
