"""Research Planner agent — decomposes a topic into a research plan.

The Planner is an *agent* (CLAUDE.md §4): it performs judgment — refining the
topic into a goal and decomposing it into prioritized, non-overlapping
sub-questions. It owns no provider-specific code; it asks the model fabric
(`ModelRouter`) for the ``PLANNING`` role and maps the structured model output
into the canonical `ResearchPlan` schema.

Why an internal output DTO: the model must never emit ids or timestamps — those
are minted by the `ResearchPlan` / `SubQuestion` default factories. The model
returns the lightweight `_PlannerOutput`; this agent constructs the persisted
schema objects from it, so identity and provenance stay owned by the schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.research_state import ResearchPlan, SubQuestion
from app.services.llm.base import ModelRole
from app.services.llm.router import ModelRouter


class PlannerError(RuntimeError):
    """Raised when the planner cannot produce a usable plan."""


SYSTEM_PROMPT = (
    "You are a research planning specialist. Given a topic, produce a focused "
    "research plan: decompose the topic into non-overlapping sub-questions that, "
    "answered together, would yield a thorough, well-grounded understanding "
    "suitable for short-form content. Order sub-questions by priority, most "
    "important first. Optionally refine the topic into a sharper goal statement. "
    "Do not answer the questions — only plan."
)


class _PlannerSubQuestion(BaseModel):
    """Model-output shape for one sub-question (no ids/timestamps)."""

    text: str
    rationale: str | None = None


class _PlannerOutput(BaseModel):
    """Structured output the ``PLANNING`` model returns.

    Deliberately distinct from the persisted `ResearchPlan`/`SubQuestion`: it
    carries only model-authored fields. Ids and timestamps are minted by the
    schema, never by the model.
    """

    goal: str | None = None
    sub_questions: list[_PlannerSubQuestion] = Field(default_factory=list)


class ResearchPlannerAgent:
    """Turns a topic into a `ResearchPlan` via the ``PLANNING``-role model."""

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def plan(self, topic: str) -> ResearchPlan:
        """Decompose ``topic`` into a prioritized `ResearchPlan`.

        Raises `PlannerError` if the model returns no sub-questions: a usable
        plan must contain at least one (per the `ResearchPlan` contract). The
        planner therefore never emits an empty plan — a populated
        `sub_questions` list is the de-facto "planner ran" signal until an
        explicit band-status field lands with the Orchestrator (M4).
        """
        model = self._router.for_role(ModelRole.PLANNING)
        output = await model.complete_structured(
            system=SYSTEM_PROMPT,
            prompt=self._build_prompt(topic),
            schema=_PlannerOutput,
        )
        if not output.sub_questions:
            raise PlannerError(f"planner returned no sub-questions for topic {topic!r}")
        return ResearchPlan(
            goal=output.goal,
            sub_questions=[
                SubQuestion(text=sq.text, rationale=sq.rationale) for sq in output.sub_questions
            ],
        )

    @staticmethod
    def _build_prompt(topic: str) -> str:
        return f"Topic to research:\n\n{topic}\n\nProduce the research plan."
