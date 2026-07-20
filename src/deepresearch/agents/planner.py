"""Planner: decomposes the research question into a typed ResearchPlan.

No model is bound here — the orchestrator passes ``model=resolve_model('planner', settings)``.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext

from ..deps import Deps
from ..models import ResearchPlan

planner_agent = Agent(
    output_type=ResearchPlan,
    deps_type=Deps,
    retries=2,
)


@planner_agent.instructions
def planner_instructions(ctx: RunContext[Deps]) -> str:
    prof = ctx.deps.settings.prof
    return f"""You are the research planner of a deep research system. Today is {ctx.deps.today}.

Decompose the user's research question into {prof.min_sub_questions}-{prof.max_sub_questions} \
sub-questions for parallel web researchers who cannot see each other's work.

Rules:
- Each sub-question must be self-contained (a researcher sees ONLY their sub-question and the \
main question) and answerable via web search.
- Sub-questions must not overlap; together they should span the main question's key facets: \
core facts, recent developments, opposing evidence or viewpoints, and practical implications \
where relevant.
- SCALE EFFORT TO THE QUESTION, not to the allowed maximum. A simple factual question \
deserves {prof.min_sub_questions} tightly-scoped sub-questions; only a genuinely multi-facet \
or contested question deserves {prof.max_sub_questions}, spanning distinct perspectives. \
Every sub-question costs real researcher time and money.
- Scope each sub-question to what one researcher can establish with about \
{prof.searches_per_researcher} web searches — narrower beats sprawling.
- Prefer questions whose answers are checkable facts over open musings.
- done_criteria: 2-5 observable statements describing what a complete answer contains.

Output only the structured plan."""


@planner_agent.output_validator
def validate_plan(ctx: RunContext[Deps], plan: ResearchPlan) -> ResearchPlan:
    prof = ctx.deps.settings.prof
    n = len(plan.sub_questions)
    if not (prof.min_sub_questions <= n <= prof.max_sub_questions):
        raise ModelRetry(
            f"You produced {n} sub-questions; produce between {prof.min_sub_questions} "
            f"and {prof.max_sub_questions}, merging overlapping ones."
        )
    if not plan.done_criteria:
        raise ModelRetry("done_criteria is empty; provide 2-5 observable completion criteria.")
    return plan
