"""Live smoke tests — cost real money (~$0.05 each). Skipped unless RUN_LIVE_TESTS=1.

These double as the standing regression test for the server-tools-through-gateway question
(plan risk #1): the gateway leg failing while direct passes means routing should be 'split'.
"""

import os

import pytest
from pydantic_ai import Agent, UsageLimits, WebSearchTool
from pydantic_ai.capabilities import NativeTool

pytestmark = pytest.mark.live

if not os.environ.get("RUN_LIVE_TESTS"):
    pytest.skip("live tests disabled (set RUN_LIVE_TESTS=1)", allow_module_level=True)

QUESTION = "In what year was the Python programming language first released? Answer with the year only."


def _search_agent() -> Agent:
    return Agent(
        capabilities=[NativeTool(WebSearchTool(max_uses=1))],
        instructions="Use web search to answer. Be terse.",
    )


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
async def test_server_side_search_direct():
    result = await _search_agent().run(
        QUESTION, model="anthropic:claude-haiku-4-5", usage_limits=UsageLimits(request_limit=4)
    )
    assert "1991" in result.output


@pytest.mark.skipif(
    not os.environ.get("PYDANTIC_AI_GATEWAY_API_KEY"), reason="needs PYDANTIC_AI_GATEWAY_API_KEY"
)
async def test_server_side_search_through_gateway():
    """THE gateway spike as a regression test. If this fails while the direct test passes,
    set DEEPRESEARCH_ROUTING=split and record the finding in JOURNAL.md."""
    result = await _search_agent().run(
        QUESTION, model="gateway/anthropic:claude-haiku-4-5", usage_limits=UsageLimits(request_limit=4)
    )
    assert "1991" in result.output
