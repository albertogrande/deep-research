"""CLI: `deepresearch "question" [flags]` — Rich Live progress, artifact summary, exit codes.

Exit codes: 0 ok · 1 fatal (no research possible) · 2 report fallback (synthesis failed) ·
3 spend refusal (gateway/provider cap) · 130 interrupted.
"""

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
    CritiqueResult,
    LiveProgress,
    PlanReady,
    ProgressEvent,
    ResearcherFailed,
    ResearcherFinished,
    ResearcherStarted,
    SynthesisStage,
    VerificationProgress,
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
    max_revise: int | None,
) -> Settings:
    overrides = {
        k: v
        for k, v in {
            "profile": depth,
            "output_dir": output,
            "max_cost": max_cost,
            "routing": routing,
            "verify": verify,
            "max_revise_iters": max_revise,
        }.items()
        if v is not None
    }
    return Settings(**overrides)  # init kwargs beat env vars beat .env


def _plain_printer() -> Callable[[ProgressEvent], None]:
    def emit(event: ProgressEvent) -> None:
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
            case VerificationProgress(done=d, total=t, pass_rate=p):
                err_console.print(f"verification: {d}/{t} sources ({p:.0%} supported)")
            case SynthesisStage(stage=stage, index=i, total=t):
                msg = stage if stage == "outline" else f"{stage} {i}/{t}"
                err_console.print(f"synthesis: {msg}")
            case CritiqueResult(verdict=v, iteration=it, n_issues=n):
                err_console.print(f"critic (pass {it}): [bold]{v}[/bold]" + (f" — {n} issue(s)" if n else ""))
            case CostUpdate(estimate_usd=est, cap_usd=cap):
                err_console.print(f"[dim]cost so far ≈ ${est:.2f} (cap ${cap:.2f})[/dim]")

    return emit


def _print_summary(record: RunRecord, report_path: str | None) -> None:
    verdict_counts: dict[str, int] = {}
    for v in record.verdicts:
        verdict_counts[v.verdict] = verdict_counts.get(v.verdict, 0) + 1
    judged = sum(n for k, n in verdict_counts.items() if k != "unverifiable")
    supported = verdict_counts.get("supported", 0)

    table = Table(title=f"run {record.run_id}", show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("claims", str(len(record.claims)))
    if record.verdicts:
        pass_rate = f" ({supported / judged:.0%} of judged supported)" if judged else ""
        table.add_row(
            "verdicts",
            " · ".join(f"{k}: {n}" for k, n in sorted(verdict_counts.items())) + pass_rate,
        )
    table.add_row("waves", f"{record.waves_run}" + (" (saturated)" if record.saturated else ""))
    if record.critique_verdict:
        revised = f", {record.critique_iterations} revise pass(es)" if record.critique_iterations else ""
        table.add_row("critic", f"{record.critique_verdict}{revised}")
    table.add_row("failed sub-questions", str(len(record.failed_sub_questions)))
    table.add_row("searches", str(record.searches_used))
    table.add_row("est. cost", f"${record.cost_estimate_usd:.2f}")
    table.add_row("duration", f"{sum(record.timings.values()):.0f}s")
    if report_path:
        table.add_row("report", report_path)
    if record.logfire_trace_id:
        table.add_row("logfire trace", record.logfire_trace_id)
    console.print(table)
    if record.limitations:
        console.print("[dim]limitations:[/dim]")
        for lim in record.limitations:
            console.print(f"[dim]  - {lim}[/dim]")


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
    max_revise: Annotated[
        int | None,
        typer.Option("--max-revise", help="Critic-driven revise passes (0 disables the critic loop; cheaper)."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print run.json to stdout.")] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Line-based progress (no live UI).")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No progress output.")] = False,
) -> None:
    """Run deep research on QUESTION and write report.md + run.json."""
    from .orchestrator import BudgetFatalError, run_research  # deferred: keeps `--help` fast

    settings = _build_settings(depth, output, max_cost, routing, False if no_verify else None, max_revise)

    silent = quiet or json_output
    use_live = not silent and not plain and err_console.is_terminal

    async def _run():
        if use_live:
            with LiveProgress(err_console, question, settings.profile, settings.routing) as live:
                return await run_research(question, settings, on_event=live.emit)
        on_event = None if silent else _plain_printer()
        return await run_research(question, settings, on_event=on_event)

    try:
        result = asyncio.run(_run())
    except KeyboardInterrupt:
        err_console.print("[red]interrupted[/red] — partial run.json flushed if a run started")
        raise typer.Exit(130) from None
    except BudgetFatalError as e:
        err_console.print(
            f"[red]aborted: spend refusal from provider/gateway[/red] ({e})\n"
            "Raise the spend cap in your Logfire gateway settings, lower --max-cost, or retry "
            "with --routing direct. Partial artifacts were written."
        )
        raise typer.Exit(3) from None
    except Exception as e:  # fatal: planning failed, credentials bad, etc.
        err_console.print(f"[red]fatal:[/red] {type(e).__name__}: {e}")
        raise typer.Exit(1) from None

    record = result.record
    if json_output:
        console.print_json(record.model_dump_json())
    elif not quiet:
        _print_summary(record, record.report_path)

    if not record.synthesis_ok:
        err_console.print("[yellow]report synthesis fell back to a claims dump (exit 2)[/yellow]")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
