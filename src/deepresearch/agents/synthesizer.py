"""Synthesizer: two-stage report writing — outline first, then one call per section.

Stage inputs are digests built by code (digest.py); citation numbers are precomputed and
fixed — the models never invent numbering, and the references block is assembled by code.
No models bound here; the orchestrator passes ``model=`` for both stages.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext

from ..deps import Deps
from ..models import Outline, SectionText

MAX_UNASSIGNED_FRACTION = 0.30

outline_agent: Agent[Deps, Outline] = Agent(
    output_type=Outline,
    deps_type=Deps,
    retries=2,
)


@outline_agent.instructions
def outline_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You are the report architect of a deep research system. Today is {ctx.deps.today}.

From the verified claims digest, design the final report:
- title: specific and informative, not clickbait.
- tldr: 2-4 sentences that directly answer the main question.
- sections: 3-6, each with a goal (what it must establish) and the claim_ids it will draw on.

Rules:
- Use ONLY claim ids from the digest. Every strong (supported) claim should find a home
  unless it is genuinely redundant.
- Order sections to answer the question first, then deepen: direct answer -> evidence and
  detail -> caveats/uncertainty. Do NOT include a references or limitations section — those
  are appended automatically."""


@outline_agent.output_validator
def validate_outline(ctx: RunContext[Deps], outline: Outline) -> Outline:
    valid = ctx.deps.outline_valid_claim_ids
    required = ctx.deps.outline_required_claim_ids
    assigned = {cid for s in outline.sections for cid in s.claim_ids}

    unknown = assigned - valid
    if unknown:
        raise ModelRetry(f"These claim_ids do not exist — remove them: {', '.join(sorted(unknown))}")
    if not outline.sections:
        raise ModelRetry("Provide at least 3 sections.")
    if required:
        unassigned = required - assigned
        if len(unassigned) / len(required) > MAX_UNASSIGNED_FRACTION:
            raise ModelRetry(
                "Too many supported claims are unassigned to any section; place these or merge "
                f"them into existing sections: {', '.join(sorted(unassigned))}"
            )
    return outline


section_agent: Agent[Deps, SectionText] = Agent(
    output_type=SectionText,
    deps_type=Deps,
    retries=2,
)


@section_agent.instructions
def section_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You write ONE section of a deep research report. Today is {ctx.deps.today}.

Rules:
- Use ONLY the claims provided; never add outside knowledge, however confident you are.
- Cite every factual sentence with the given [n] markers (e.g. "... rose 12% in 2025 [3].").
- A claim marked with † is unverified — cite it as [n]† and attribute it ("according to ...").
- A claim with verdict 'partial' must be stated with hedged language ("approximately",
  "reportedly", "at least").
- Write tight, information-dense prose in Markdown. No section heading (added by the
  assembler), no bullet-point dumps unless the content is genuinely enumerable, no filler.
- 1-4 paragraphs. If claims contradict each other, present both sides with their citations."""
