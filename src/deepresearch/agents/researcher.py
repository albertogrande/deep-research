"""Researcher: answers one sub-question with server-side web search/fetch, returns Findings.

Built by a cached factory because ``max_uses`` on WebSearchTool is fixed at construction
time and varies by depth profile. No model is bound — the orchestrator passes ``model=``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent, ModelRetry, RunContext, WebFetchTool, WebSearchTool
from pydantic_ai.capabilities import NativeTool

from ..deps import Deps
from ..models import Findings

FETCH_CONTENT_TOKEN_CAP = 20_000


@lru_cache(maxsize=8)
def researcher_agent(max_searches: int) -> Agent[Deps, Findings]:
    agent: Agent[Deps, Findings] = Agent(
        output_type=Findings,
        deps_type=Deps,
        retries=2,
        capabilities=[
            NativeTool(WebSearchTool(max_uses=max_searches)),
            NativeTool(WebFetchTool(enable_citations=True, max_content_tokens=FETCH_CONTENT_TOKEN_CAP)),
        ],
    )

    @agent.instructions
    def researcher_instructions(ctx: RunContext[Deps]) -> str:
        return f"""You are one of several parallel web researchers in a deep research system. \
Today is {ctx.deps.today}. You see only YOUR sub-question — be thorough on it and only it.

Method:
- Search the web (up to {max_searches} searches, so make each query count; vary angles rather \
than rephrasing). Fetch promising sources for detail when the search snippets are not enough.
- Prefer primary and authoritative sources (official docs, papers, filings, reputable press) \
over aggregators; prefer recent sources for time-sensitive facts.
- Extract findings as ATOMIC claims: one checkable fact per claim, each with a VERBATIM \
supporting quote from the source, the exact source URL, the source title, and your confidence.
- Do not invent quotes or URLs. A claim you cannot back with a quote from a real source does \
not belong in the output.
- Record dead ends, paywalls, contradictions between sources, and anything a coordinator \
should know in `notes`. If you find genuinely nothing, return zero claims and explain why in \
`notes` — that is a valid result.
- End with `summary`: 2-4 sentences stating what you established for your sub-question and \
what you could not — this becomes the coordinator's compressed view of your work.
- If the prompt includes an ALREADY ESTABLISHED block, do not re-research any of it; \
target exactly the gap your sub-question names.

Quality over quantity: 3-8 strong claims beat 20 weak ones."""

    @agent.output_validator
    def validate_findings(ctx: RunContext[Deps], findings: Findings) -> Findings:
        bad: list[str] = []
        for i, claim in enumerate(findings.claims):
            if not claim.supporting_quote.strip():
                bad.append(f"claim {i}: empty supporting_quote")
            if not claim.source_url.startswith(("http://", "https://")):
                bad.append(f"claim {i}: source_url is not a valid http(s) URL")
            if not claim.statement.strip():
                bad.append(f"claim {i}: empty statement")
        if bad:
            raise ModelRetry(
                "Fix these claims (drop any you cannot back with a real quote and URL): " + "; ".join(bad)
            )
        return findings

    return agent
