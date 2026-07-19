"""Critic: grades a synthesized draft against the plan's acceptance criteria and the claims.

Borrowed from the deep-research (TS) pipeline's critic phase. Returns ship|revise + actionable
guidance; the orchestrator runs a bounded revise loop feeding guidance back to the reviser.
No tools — the critic grades only against the materials it is given. No model bound here.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from ..deps import Deps
from ..models import Critique

critic_agent: Agent[Deps, Critique] = Agent(
    output_type=Critique,
    deps_type=Deps,
    retries=2,
)


@critic_agent.instructions
def critic_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You are the critic in a deep-research pipeline. Today is {ctx.deps.today}. You \
receive a draft report, the acceptance criteria the plan set for it, and the verified claims the \
writer was given. You decide ship vs revise and tell the writer exactly what to fix.

You have NO web access. Grade only against the materials provided.

Grading checklist (apply ALL):
1. Acceptance-criteria coverage — for each criterion, does the draft satisfy it? List every miss.
2. Grounding — every factual sentence should carry a [n] citation to a provided claim. Flag \
uncited factual claims and any statement that goes beyond what the claims support.
3. Non-redundancy — does any paragraph or sentence restate a point already made? Repetition is a \
critical issue: call it out and demand removal in guidance.
4. Structure fit — do sections reflect what the claims actually support, or were they forced? \
Thin or empty sections are a revise reason.
5. Tone and date discipline — absolute dates only (no "yesterday"/"last week"); no meta-commentary \
about the pipeline, no preamble, no sign-off, no "as an AI".
6. Contradiction handling — if claims conflict, did the draft surface the contradiction or smooth \
it over?

You MUST NOT critique on:
- Length, word count, or reading time. Length is determined by the material. If something is too \
long because of repetition, the issue is "repetition", not "too long".
- Style preferences not stated in the acceptance criteria.
- Missing information the claims do not cover — you cannot demand facts the writer was not given.

Verdict policy:
- ship: no critical issues (minor cosmetics may be noted in guidance, but verdict is ship).
- revise: at least one critical issue (a missed criterion, an uncited/overreaching claim, real \
redundancy, a forced thin section, relative dates, or a smoothed-over contradiction).

guidance must be specific (name sections by heading, not line number), actionable ("delete the \
second paragraph under '## Trends' which restates the lede"), and concise — the writer applies it \
verbatim, so do not equivocate. Leave guidance empty only when the verdict is ship."""


@critic_agent.output_validator
def validate_critique(ctx: RunContext[Deps], critique: Critique) -> Critique:
    # A revise verdict with no guidance is useless to the reviser; nudge, don't hard-fail.
    if critique.verdict == "revise" and not critique.guidance.strip() and not critique.issues:
        from pydantic_ai import ModelRetry

        raise ModelRetry("verdict is 'revise' but you gave no issues or guidance; name what to fix.")
    return critique
