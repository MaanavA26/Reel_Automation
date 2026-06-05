"""Pluggable quality scorers — "is this output *good*", beyond schema-pass.

Schema-pass and latency are mechanical (the harness owns them). *Quality* is a
judgment, so it is pluggable behind the `Judge` protocol with two built-ins:

- `RuleBasedJudge` — the deterministic default. A pure function over the parsed
  output (no model, no network), so an eval run is fully hermetic out of the box.
- `ModelJudge` — the optional LLM-as-judge (CLAUDE.md §6: "LLM as a judge to find
  the best LLM per task"). It calls an *independent* model and is guarded against
  judging its own output — the structural fix for the self-judge score inflation
  observed in ``docs/llm-model-selection.md`` §3 (``gpt-oss-120b`` self-scored 5.0,
  discounted). Judging is reasoning, but it is plugged into a deterministic
  scoring loop — the agent/tool split of CLAUDE.md §4 kept intact.

A judge returns a `QualityScore` (a float plus an explanation) or ``None`` when no
quality dimension applies (then only schema-pass + latency rank the candidates).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field

from app.eval.task import EvalTask
from app.services.llm.base import ModelProvider


class JudgeError(RuntimeError):
    """Raised on a misconfigured judge (e.g. a judge that judges itself)."""


class QualityScore(BaseModel):
    """A single quality judgment for one candidate output on one task."""

    model_config = {"extra": "forbid"}

    score: float = Field(description="Quality on the judge's scale (higher is better).")
    rationale: str | None = Field(default=None, description="Why this score.")


class Judge(Protocol):
    """A pluggable quality scorer for a candidate's structured output.

    ``score`` is async to admit a model-backed judge (network I/O), matching the
    `ModelProvider` contract; the rule-based default simply returns immediately.
    """

    async def score(self, *, task: EvalTask[BaseModel], output: BaseModel) -> QualityScore: ...


class RuleBasedJudge:
    """Deterministic, model-free quality scorer (the hermetic default).

    Delegates to a caller-supplied ``score_fn`` over the parsed output, so the
    judge is generic over the task schema and needs no model. With no ``score_fn``
    it returns a constant baseline — useful when only schema-pass + latency should
    rank candidates but a `Judge` is still required by the harness signature.
    """

    def __init__(
        self,
        score_fn: Callable[[BaseModel], QualityScore] | None = None,
    ) -> None:
        self._score_fn = score_fn

    async def score(self, *, task: EvalTask[BaseModel], output: BaseModel) -> QualityScore:
        if self._score_fn is None:
            return QualityScore(score=0.0, rationale="no rule-based scorer configured")
        return self._score_fn(output)


# How a `ModelJudge` renders a candidate's output into the judge prompt. Default
# is the output's JSON; a caller may pass a task-aware renderer for richer prompts.
OutputRenderer = Callable[[EvalTask[BaseModel], BaseModel], str]


def _default_renderer(task: EvalTask[BaseModel], output: BaseModel) -> str:
    return output.model_dump_json(indent=2)


class ModelJudge:
    """LLM-as-judge: an *independent* model scores the candidate's output.

    Constructed with its own ``(provider, model)`` and a 0..``scale`` rubric in the
    system prompt. ``assert_independent_of`` raises `JudgeError` if asked to judge
    output produced by the *same* ``(provider, model)`` — making the §3 self-judge
    caveat a structural guarantee rather than a footnote.
    """

    SYSTEM_PROMPT = (
        "You are an impartial evaluation judge. You will be shown a task and one "
        "model's structured answer to it. Score the answer's quality — coverage, "
        "specificity, and faithfulness to the task — on the given integer scale, "
        "highest = best. Judge only the answer shown; do not solve the task "
        "yourself. Return the score and a one-sentence rationale."
    )

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        scale: int = 5,
        render_output: OutputRenderer = _default_renderer,
    ) -> None:
        self._provider = provider
        self._model = model
        self._scale = scale
        self._render_output = render_output

    def assert_independent_of(self, *, provider: str, model: str) -> None:
        """Raise `JudgeError` if this judge would score its own output.

        Self-judging inflates scores (``docs/llm-model-selection.md`` §3); the
        harness calls this for every candidate so the misconfiguration fails loud
        rather than silently skewing the ranking.
        """
        if self._provider.name == provider and self._model == model:
            raise JudgeError(
                f"judge model {provider}/{model} cannot judge its own output "
                "(self-judging inflates quality scores; use an independent model)"
            )

    async def score(self, *, task: EvalTask[BaseModel], output: BaseModel) -> QualityScore:
        # The judge's structured output *is* a `QualityScore` (score + rationale),
        # so no separate wire DTO is needed — the provider returns it directly.
        return await self._provider.complete_structured(
            model=self._model,
            system=f"{self.SYSTEM_PROMPT}\n\nScale: integer 0 (worst) to {self._scale} (best).",
            prompt=self._build_prompt(task, output),
            schema=QualityScore,
        )

    def _build_prompt(self, task: EvalTask[BaseModel], output: BaseModel) -> str:
        return (
            f"Task: {task.name}\n\n"
            f"Task instructions:\n{task.system}\n\n"
            f"Task prompt:\n{task.prompt}\n\n"
            f"Model's answer:\n{self._render_output(task, output)}\n\n"
            "Score this answer."
        )
