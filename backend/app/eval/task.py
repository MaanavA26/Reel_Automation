"""The eval task — one schema-bound LLM job to score candidate models against.

An `EvalTask` is the deterministic *unit of work* the harness runs each
candidate `(provider, model)` against: a fixed ``(system, prompt, schema)``
triple, exactly the shape `ModelProvider.complete_structured` consumes. It is
deliberately schema-agnostic — the harness scores *any* structured task, not the
Research Planner specifically (CLAUDE.md §4: the harness is a deterministic
service; the agent under test is referenced, never invoked, by the harness core).

Mirrors the methodology of ``docs/llm-model-selection.md`` (§3, §6): enumerate a
task, run it across candidate models, score schema-pass / latency / quality. The
task carries the *inputs*; scoring lives in `report.py` / `judge.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic

from app.services.llm.base import ModelProvider, StructuredT

# A named registry of provider adapters — the same shape `ModelRouter` consumes,
# so an eval run and a live run draw from one provider set. The harness selects
# candidates explicitly by name (not by role/policy), so no `RolePolicy` here.
ProviderRegistry = Mapping[str, ModelProvider]


@dataclass(frozen=True)
class EvalTask(Generic[StructuredT]):
    """One structured LLM job: a fixed prompt that must yield ``schema``.

    Generic over the Pydantic ``schema`` the candidate must produce, so the
    harness stays task-neutral and type-checks each task's scorer against the
    right output type. ``name`` labels the task in the report (e.g. a sample
    topic, mirroring the §3 "3 diverse topics" method).
    """

    name: str
    system: str
    prompt: str
    schema: type[StructuredT]
