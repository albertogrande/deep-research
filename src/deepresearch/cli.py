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
from .models import PlannedSubQuestion, ResearchPlan, RunRecord
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


class ConsoleInteraction:
    """Console prompts behind the InteractionHooks protocol. Blocking is fine here: both
    gates run before any researcher fan-out, so nothing concurrent is stalled. Ctrl-C at
    either prompt aborts pre-spend (exit 130)."""

    async def clarify(self, questions: list[str]) -> list[str]:
        err_console.print("\n[bold]Before researching, a few clarifications[/bold] (Enter to skip):")
        return [typer.prompt(f"  {q}", default="", show_default=False) for q in questions]

    async def review_plan(self, plan: ResearchPlan) -> ResearchPlan:
        while True:
            err_console.print("\n[bold]Research plan[/bold] — edit before any money is spent:")
            for i, sq in enumerate(plan.sub_questions, 1):
                err_console.print(f"  {i}. {sq.question}")
            cmd = typer.prompt(
                "a <text> add · d <n> drop · e <n> <text> edit · g go", default="g", show_default=False
            ).strip()
            if cmd in ("", "g"):
                return plan
            op, _, rest = cmd.partition(" ")
            questions = list(plan.sub_questions)
            try:
                if op == "a" and rest.strip():
                    questions.append(PlannedSubQuestion(question=rest.strip(), rationale="added by user"))
                elif op == "d":
                    if len(questions) <= 1:
                        err_console.print("[red]keep at least one sub-question[/red]")
                        continue
                    questions.pop(int(rest) - 1)
                elif op == "e":
                    n, _, text = rest.partition(" ")
                    idx = int(n) - 1
                    if not text.strip():
                        raise ValueError
                    questions[idx] = PlannedSubQuestion(
                        question=text.strip(), rationale=questions[idx].rationale
                    )
                else:
                    err_console.print("[red]unknown command[/red]")
                    continue
            except (ValueError, IndexError):
                err_console.print("[red]bad command[/red]")
                continue
            plan = ResearchPlan(sub_questions=questions, done_criteria=plan.done_criteria)


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
    cache_read = sum(u.cache_read_tokens for u in record.usage.values())
    cache_write = sum(u.cache_write_tokens for u in record.usage.values())
    if cache_read or cache_write:
        table.add_row("prompt cache", f"read {cache_read:,} tok · write {cache_write:,} tok")
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
    question: Annotated[
        str | None, typer.Argument(help="The research question (omit when using --resume).")
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume an interrupted run from its runs/<id> directory."),
    ] = None,
    depth: Annotated[
        str | None, typer.Option("--depth", "-d", help=f"One of: {', '.join(PROFILES)}.")
    ] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output directory.")] = None,
    max_cost: Annotated[float | None, typer.Option("--max-cost", help="USD cap for this run.")] = None,
    routing: Annotated[str | None, typer.Option("--routing", help="gateway | direct | split.")] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive", "-i", help="Ask clarifying questions and let you edit the plan pre-spend."
        ),
    ] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", help="Skip claim verification.")] = False,
    max_revise: Annotated[
        int | None,
        typer.Option(
            "--max-revise", help="Critic-driven revise passes (0 disables the critic loop; cheaper)."
        ),
    ] = None,
    html_out: Annotated[
        bool, typer.Option("--html", help="Also render report.html next to report.md.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print run.json to stdout.")] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Line-based progress (no live UI).")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No progress output.")] = False,
) -> None:
    """Run deep research on QUESTION (or continue an interrupted run with --resume) and
    write report.md + run.json."""
    from pathlib import Path

    from .orchestrator import BudgetFatalError, run_research  # deferred: keeps `--help` fast

    if (question is None) == (resume is None):
        err_console.print("[red]provide either QUESTION or --resume RUN_DIR (not both)[/red]")
        raise typer.Exit(1)

    settings = _build_settings(depth, output, max_cost, routing, False if no_verify else None, max_revise)
    resume_from = Path(resume) if resume else None
    headline = question or f"resume: {resume}"
    interaction = ConsoleInteraction() if interactive else None

    silent = quiet or json_output
    # Interactive prompts and a Live redraw loop cannot share the terminal.
    use_live = not silent and not plain and not interactive and err_console.is_terminal

    async def _run():
        if use_live:
            with LiveProgress(err_console, headline, settings.profile, settings.routing) as live:
                return await run_research(
                    question or "", settings, on_event=live.emit, resume_from=resume_from
                )
        on_event = None if silent else _plain_printer()
        return await run_research(
            question or "", settings, on_event=on_event, resume_from=resume_from, interaction=interaction
        )

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
    html_path = None
    if html_out and result.report_path is not None:
        from .artifacts import render_html, write_html

        md_text = result.report_path.read_text(encoding="utf-8")
        title = next(
            (line[2:].strip() for line in md_text.splitlines() if line.startswith("# ")), record.run_id
        )
        html_path = write_html(render_html(md_text, title=title), result.run_dir)

    if json_output:
        console.print_json(record.model_dump_json())
    elif not quiet:
        _print_summary(record, record.report_path)
        if html_path is not None:
            console.print(f"[dim]html: {html_path}[/dim]")

    if not record.synthesis_ok:
        err_console.print("[yellow]report synthesis fell back to a claims dump (exit 2)[/yellow]")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
