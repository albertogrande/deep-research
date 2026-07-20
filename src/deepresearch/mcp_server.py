"""deepresearch as an MCP server: any MCP client (Claude Desktop, Claude Code, ...) gets a
`deep_research` tool that runs the full pipeline and returns the cited Markdown report.

A thin surface only — `run_research` owns the pipeline. Requires the `mcp` extra:
`uv add "deepresearch[mcp]"` (or `pip install "deepresearch[mcp]"`).
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "The MCP surface needs the `mcp` package — install the extra: "
        'uv add "deepresearch[mcp]" (or pip install "deepresearch[mcp]")'
    ) from e

from .config import PROFILES, Settings
from .orchestrator import run_research

server = FastMCP("deepresearch")


@server.tool()
async def deep_research(question: str, depth: str = "standard", max_cost: float | None = None) -> str:
    """Run deep web research and return a fully cited Markdown report.

    The pipeline plans sub-questions, fans out parallel web researchers, adversarially
    re-verifies every claim against its source, and synthesizes a report whose citation
    numbering is code-owned. Costs real money (web search + tokens); a `standard` run is
    roughly $0.55-0.90.

    Args:
        question: The research question.
        depth: One of quick | standard | deep — controls waves, researcher count, and models.
        max_cost: USD cap for this run; defaults to the depth profile's cap.
    """
    if depth not in PROFILES:
        return f"error: unknown depth {depth!r}; expected one of {sorted(PROFILES)}"
    overrides = {"max_cost": max_cost} if max_cost is not None else {}
    settings = Settings(profile=depth, **overrides)

    result = await run_research(question, settings)
    record = result.record
    footer = (
        f"\n\n---\nrun {record.run_id} · {len(record.claims)} claims · "
        f"{len(record.verdicts)} verdicts · est. cost ${record.cost_estimate_usd:.2f} · "
        f"artifacts: {result.run_dir}"
    )
    if result.report_path is None:
        return "The run produced no report (no usable claims were gathered)." + footer
    return result.report_path.read_text(encoding="utf-8") + footer


def main() -> None:
    server.run()  # stdio transport


if __name__ == "__main__":
    main()
