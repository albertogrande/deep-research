"""Gap analyst: reviews aggregate findings vs. the plan, decides saturated-or-iterate.

Sees a code-built digest (claim statements, counts, notes — never quotes or full URLs) plus
the list of already-asked questions. No model bound; orchestrator passes ``model=``.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext

from ..deps import Deps
from ..models import GapAnalysis

MAX_FOLLOW_UPS = 4

gap_analyst_agent = Agent(
    output_type=GapAnalysis,
    deps_type=Deps,
    retries=2,
)


@gap_analyst_agent.instructions
def gap_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You are the research coordinator of a deep research system. Today is \
{ctx.deps.today}. You receive the research plan and a workspace digest of the claims gathered \
so far by parallel researchers: each sub-question carries its researcher's own summary, and \
claims added by the latest wave are marked [NEW] and repeated in the NEW THIS WAVE section.

Decide whether the research is SATURATED — i.e. another round of web research would not \
materially change the final report — or whether specific gaps remain.

Rules:
- Judge coverage against the plan's done_criteria, not against perfection. Mark each \
sub-question covered / partial / uncovered.
- If not saturated: emit at most {MAX_FOLLOW_UPS} follow-up questions targeting the most \
important gaps ONLY. A good follow-up is narrower than the original sub-question, searchable, \
and must NOT restate any already-asked question (the digest lists them).
- Contradictions between sources are gaps worth a follow-up; missing minor color is not.
- If saturated: follow_up_questions must be empty.
- Be decisive. An extra wave costs real money; demand it only when the report would be \
visibly incomplete without it."""


@gap_analyst_agent.output_validator
def validate_gap(ctx: RunContext[Deps], gap: GapAnalysis) -> GapAnalysis:
    if gap.saturated and gap.follow_up_questions:
        raise ModelRetry("You said saturated=true but provided follow_up_questions; make them consistent.")
    if len(gap.follow_up_questions) > MAX_FOLLOW_UPS:
        raise ModelRetry(
            f"You provided {len(gap.follow_up_questions)} follow-ups; keep only the "
            f"{MAX_FOLLOW_UPS} most important."
        )
    return gap
