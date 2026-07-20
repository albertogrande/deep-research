"""Shared eval types and the task function under evaluation.

The task runs the full pipeline at quick depth and returns the report text plus the full
RunRecord, so evaluators read structured run data instead of mining spans.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from deepresearch.config import Settings, provider_prefix
from deepresearch.models import RunRecord
from deepresearch.orchestrator import run_research

EVAL_RUNS_DIR = Path("runs-evals")


class EvalMeta(BaseModel):
    category: str
    notes: str = ""


class EvalOutput(BaseModel):
    report_markdown: str
    record: RunRecord


def judge_model(settings: Settings) -> str:
    """Judge model for LLMJudge — MUST be explicit: the library default is an OpenAI model
    and this project has no OpenAI key. Reuses the routing prefix logic (never a server-tool
    role) so it can't drift from ``resolve_model``."""
    return provider_prefix(settings, server_tool=False) + "claude-sonnet-4-6"


def install_default_judge(settings: Settings) -> None:
    """Belt and braces against the OpenAI-default trap: even if some evaluator forgets an
    explicit ``model=``, the process-wide default judge is ours."""
    from pydantic_evals.evaluators.llm_as_a_judge import set_default_judge_model

    set_default_judge_model(judge_model(settings))  # type: ignore[arg-type]


def make_task(base_settings: Settings):
    """Build the async eval task closed over settings (verify on/off, routing, depth)."""

    async def research_task(question: str) -> EvalOutput:
        result = await run_research(question, base_settings)
        report = result.report_path.read_text(encoding="utf-8") if result.report_path else ""
        return EvalOutput(report_markdown=report, record=result.record)

    return research_task


def eval_settings(*, verify: bool = True, out_subdir: str = "default", profile: str = "quick") -> Settings:
    """Settings for eval runs. Default quick depth for cost control (~8 cases × ≈$0.30);
    pass profile='standard' to evaluate the depth real users run (~8 × ≈$0.70)."""
    return Settings(
        profile=profile,
        verify=verify,
        output_dir=str(EVAL_RUNS_DIR / out_subdir),
    )
