"""Clarifier: asks 0-3 questions whose answers would change the research plan.

Part of planning — the orchestrator runs it on the planner's model and bills the planner
ledger (no separate role, so profiles and the RunRecord schema stay stable). No model bound;
the orchestrator passes ``model=``.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext

from ..deps import Deps
from ..models import ClarifyingQuestions

MAX_CLARIFYING_QUESTIONS = 3

clarifier_agent = Agent(
    output_type=ClarifyingQuestions,
    deps_type=Deps,
    retries=2,
)


@clarifier_agent.instructions
def clarifier_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You decide whether a research question needs clarification before a deep \
research system spends money on it. Today is {ctx.deps.today}.

Ask at most {MAX_CLARIFYING_QUESTIONS} questions, and ONLY where the answer would change the \
research plan: ambiguous scope (which region, market, timeframe?), an unstated comparison \
baseline, a term with several readings, or a missing success criterion.

Prefer zero questions. A clear, self-contained question deserves an empty list — never ask a \
filler question to look thorough."""


@clarifier_agent.output_validator
def validate_clarifying(ctx: RunContext[Deps], out: ClarifyingQuestions) -> ClarifyingQuestions:
    if len(out.questions) > MAX_CLARIFYING_QUESTIONS:
        raise ModelRetry(
            f"You asked {len(out.questions)} questions; keep only the "
            f"{MAX_CLARIFYING_QUESTIONS} most plan-changing."
        )
    return out
