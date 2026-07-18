"""CLI: `deepresearch "question" [flags]`. Plain-line progress in Phase 1; Rich Live in Phase 5."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .config import PROFILES, Settings
from .models import RunRecord
from .progress import (
    CostUpdate,
    PlanReady,
    ProgressEvent,
    ResearcherFailed,
    ResearcherFinished,
    ResearcherStarted,
    WaveStarted,
)

app = typer.Typer(add_completion=False, rich_markup_mode="rich")
console = Console(stderr=False)
err_console = Console(stderr=True)


def _build_settings(
    depth: str | None,
    output: str | None,
    max_cost: float | None,
    routing: str | None,
    verify: bool | None,
) -> Settings:
    overrides = {
        k: v
        for k, v in {
            "profile": depth,
            "output_dir": output,
            "max_cost": max_cost,
            "routing": routing,
            "verify": verify,
        }.items()
        if v is not None
    }
    return Settings(**overrides)  # init kwargs beat env vars beat .env


def _plain_printer(quiet: bool) -> Callable[[ProgressEvent], None]:
    def emit(event: ProgressEvent) -> None:
        if quiet:
            return
        match event:
            case PlanReady(n_sub_questions=n):
                err_console.print(f"[bold]plan[/bold]: {n} sub-questions")
            case WaveStarted(wave=w, n_questions=n):
                err_console.print(f"[bold]wave {w}[/bold]: launching {n} researchers")
            case ResearcherStarted(sub_question_id=sq_id, question=q):
                err_console.print(f"  {sq_id} researching: {q[:80]}")
            case ResearcherFinished(sub_question_id=sq_id, claims_found=n):
                err_console.print(f"  {sq_id} done: {n} claims")
            case ResearcherFailed(sub_question_id=sq_id, reason=r):
                err_console.print(f"  [red]{sq_id} failed[/red]: {r}")
            case CostUpdate(estimate_usd=est, cap_usd=cap):
                err_console.print(f"[dim]cost so far ≈ ${est:.2f} (cap ${cap:.2f})[/dim]")

    return emit


def _print_summary(record: RunRecord) -> None:
    table = Table(title=f"claims — {record.run_id}", show_lines=False)
    table.add_column("id", style="dim")
    table.add_column("statement", max_width=70)
    table.add_column("source")
    table.add_column("conf", justify="center")
    for claim in record.claims:
        table.add_row(claim.id, claim.statement, claim.source_url, claim.confidence)
    console.print(table)
    console.print(
        f"claims: {len(record.claims)} | failed sub-questions: {len(record.failed_sub_questions)} "
        f"| searches: {record.searches_used} | est. cost: ${record.cost_estimate_usd:.2f}"
    )


@app.command()
def research(
    question: Annotated[str, typer.Argument(help="The research question.")],
    depth: Annotated[
        str | None, typer.Option("--depth", "-d", help=f"One of: {', '.join(PROFILES)}.")
    ] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output directory.")] = None,
    max_cost: Annotated[float | None, typer.Option("--max-cost", help="USD cap for this run.")] = None,
    routing: Annotated[str | None, typer.Option("--routing", help="gateway | direct | split.")] = None,
    no_verify: Annotated[bool, typer.Option("--no-verify", help="Skip claim verification.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print run.json to stdout.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No progress output.")] = False,
) -> None:
    """Run deep research on QUESTION and write report.md + run.json."""
    from .orchestrator import run_research  # deferred: keeps `--help` fast

    settings = _build_settings(depth, output, max_cost, routing, False if no_verify else None)
    try:
        result = asyncio.run(run_research(question, settings, on_event=_plain_printer(quiet or json_output)))
    except KeyboardInterrupt:
        err_console.print("[red]interrupted[/red] — partial run.json flushed if a run started")
        raise typer.Exit(130) from None

    if json_output:
        console.print_json(result.record.model_dump_json())
    elif not quiet:
        _print_summary(result.record)
        console.print(f"artifacts: {result.run_dir}")


if __name__ == "__main__":
    app()
