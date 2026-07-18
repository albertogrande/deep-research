"""Spike: do Anthropic server-side web tools work through the Pydantic AI Gateway?

This is THE open architecture question (plan risk #1). The gateway proxies requests in
native Anthropic format, so server-side tools (web_search / web_fetch, executed on
Anthropic's infrastructure) *should* pass through — but it is undocumented, so we test.

Run it once per new environment (needs PYDANTIC_AI_GATEWAY_API_KEY and/or ANTHROPIC_API_KEY):

    uv run python scripts/spike_gateway_server_tools.py

Outcome A (both legs pass): keep routing="gateway" as default.
Outcome B (direct passes, gateway fails): set DEEPRESEARCH_ROUTING=split — researcher and
verifier go direct to Anthropic, every other role stays on the gateway.
Record the verdict in JOURNAL.md either way. Cost per leg: 1 search ≈ $0.01 + a few k haiku
tokens. Also check Logfire afterwards: both runs should appear as traces if LOGFIRE_TOKEN set.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic_ai import Agent, UsageLimits, WebSearchTool
from pydantic_ai.capabilities import NativeTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deepresearch.telemetry import setup_telemetry  # noqa: E402

QUESTION = "In what year was the Python programming language first released? Answer with the year only."


async def try_leg(label: str, model: str) -> bool:
    print(f"\n=== {label}: {model} ===")
    agent = Agent(
        model,
        capabilities=[NativeTool(WebSearchTool(max_uses=1))],
        instructions="Use web search to answer. Be terse.",
    )
    try:
        result = await agent.run(QUESTION, usage_limits=UsageLimits(request_limit=4))
    except Exception as e:  # noqa: BLE001 — a spike wants the raw failure, whatever it is
        print(f"FAILED: {type(e).__name__}: {e}")
        return False
    usage = result.usage
    print(f"OK: {result.output!r}")
    print(f"usage: requests={usage.requests} in={usage.input_tokens} out={usage.output_tokens}")
    details = getattr(usage, "details", None) or {}
    print(f"usage details (look for web search counts): {details}")
    return True


async def main() -> None:
    setup_telemetry(console=True)
    results: dict[str, bool] = {}

    if os.environ.get("PYDANTIC_AI_GATEWAY_API_KEY"):
        results["gateway"] = await try_leg("via Gateway", "gateway/anthropic:claude-haiku-4-5")
    else:
        print("skipping gateway leg: PYDANTIC_AI_GATEWAY_API_KEY not set")

    if os.environ.get("ANTHROPIC_API_KEY"):
        results["direct"] = await try_leg("direct Anthropic", "anthropic:claude-haiku-4-5")
    else:
        print("skipping direct leg: ANTHROPIC_API_KEY not set")

    print("\n=== VERDICT ===")
    if not results:
        print("No keys available — nothing tested. Set keys in .env and re-run.")
    elif results.get("gateway"):
        print("Server-side web search WORKS through the gateway. Keep routing=gateway (default).")
    elif "gateway" in results and results.get("direct"):
        print("Gateway leg FAILED but direct works. Set DEEPRESEARCH_ROUTING=split and journal it.")
    elif "gateway" in results:
        print("Both legs failed — check keys/network before concluding anything about the gateway.")
    else:
        print(f"Partial data (direct only: {results.get('direct')}). Re-run with a gateway key to decide.")


if __name__ == "__main__":
    asyncio.run(main())
