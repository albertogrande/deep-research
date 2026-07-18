"""Verifier: re-fetches one cited source and adversarially checks every claim citing it.

Per-SOURCE batching (not per-claim): the fetch is the scarce operation, and claims cluster on
sources. The source URL is embedded in the prompt, which is what makes it fetchable by the
server-side WebFetchTool (fetch only works on in-context URLs). No model bound here.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext, WebFetchTool
from pydantic_ai.capabilities import NativeTool

from ..deps import Deps
from ..models import Claim, SourceVerification

FETCH_CONTENT_TOKEN_CAP = 16_000

verifier_agent: Agent[Deps, SourceVerification] = Agent(
    output_type=SourceVerification,
    deps_type=Deps,
    retries=2,
    capabilities=[
        NativeTool(
            WebFetchTool(max_uses=2, enable_citations=True, max_content_tokens=FETCH_CONTENT_TOKEN_CAP)
        )
    ],
)


@verifier_agent.instructions
def verifier_instructions(ctx: RunContext[Deps]) -> str:
    return f"""You are the fact verifier of a deep research system. Today is {ctx.deps.today}.

You get ONE source URL and the claims that cite it. Fetch the URL, then judge each claim
STRICTLY against what the fetched page actually says:

- supported: the page clearly supports the statement, and the supporting quote appears in the
  page verbatim or as a close paraphrase.
- partial: directionally right but overstated, imprecise, or the quote does not quite match.
- unsupported: the page contradicts the statement or contains nothing to support it.
- unverifiable: use ONLY when the fetch failed (error, paywall, access denied, empty content).

Rules:
- If the fetch fails in any way: set fetch_ok=false and mark EVERY claim unverifiable. Do not
  guess from memory — your world knowledge is not the source.
- Judge only against the fetched content. A claim being true in general but absent from this
  page is unsupported.
- Return a verdict for EVERY claim_id you were given, with one-sentence reasoning each."""


@verifier_agent.output_validator
def validate_verification(ctx: RunContext[Deps], sv: SourceVerification) -> SourceVerification:
    if not sv.fetch_ok:
        bad = [v.claim_id for v in sv.verdicts if v.verdict != "unverifiable"]
        if bad:
            raise ModelRetry(
                f"fetch_ok=false, so every verdict must be 'unverifiable'; fix: {', '.join(bad)}"
            )
    return sv


def verify_prompt(source_url: str, claims: list[Claim]) -> str:
    lines = [f"SOURCE URL TO FETCH AND CHECK:\n{source_url}", "", "CLAIMS CITING THIS SOURCE:"]
    for c in claims:
        lines.append(f"\nclaim_id: {c.id}")
        lines.append(f"  statement: {c.statement}")
        lines.append(f'  supporting_quote: "{c.supporting_quote}"')
    return "\n".join(lines)
