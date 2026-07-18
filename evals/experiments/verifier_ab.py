"""The flagship experiment: does claim verification earn its cost?

Runs the same dataset twice — verifier ON vs OFF — as two named Logfire experiments, then
prints an objective delta table (citation coverage, URL resolution, cost, duration) plus the
on-arm-only verification metrics.

    uv run python -m evals.experiments.verifier_ab [--max-concurrency 2]

LIVE and ~2× the cost of one eval run (≈$5 total at quick depth). Judges are off by default
here; add --judges to also compare faithfulness scores (extra cost).
"""

from __future__ import annotations

import argparse
import asyncio
from statistics import mean

from rich.console import Console
from rich.table import Table

from deepresearch.telemetry import setup_telemetry

from ..common import EvalOutput, eval_settings, judge_model, make_task
from ..evaluators import (
    CitationCoverage,
    UnverifiableRate,
    URLResolution,
    VerifiedClaimRate,
    report_body_paragraphs,
)
from ..run_evals import judge_evaluators, load_dataset

console = Console()


def _capture_task(settings, sink: list[EvalOutput]):
    inner = make_task(settings)

    async def task(question: str) -> EvalOutput:
        out = await inner(question)
        sink.append(out)
        return out

    return task


def _mean_citation_coverage(outputs: list[EvalOutput]) -> float:
    import re

    cite = re.compile(r"\[\d+\]")
    values = []
    for o in outputs:
        paragraphs = report_body_paragraphs(o.report_markdown)
        if paragraphs:
            values.append(sum(1 for p in paragraphs if cite.search(p)) / len(paragraphs))
    return mean(values) if values else 0.0


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--judges", action="store_true", help="also run LLM judges on both arms")
    args = parser.parse_args()

    setup_telemetry()
    arms: dict[str, list[EvalOutput]] = {"verifier-on": [], "verifier-off": []}

    for arm, verify in (("verifier-on", True), ("verifier-off", False)):
        settings = eval_settings(verify=verify, out_subdir=arm)
        dataset = load_dataset()
        dataset.evaluators.extend(
            [CitationCoverage(), URLResolution()]
            + ([VerifiedClaimRate(), UnverifiableRate()] if verify else [])
        )
        if args.judges:
            dataset.evaluators.extend(judge_evaluators(judge_model(settings)))
        console.rule(f"[bold]{arm}")
        report = await dataset.evaluate(
            _capture_task(settings, arms[arm]), name=arm, max_concurrency=args.max_concurrency
        )
        report.print(include_input=False, include_output=False, include_durations=True)

    on, off = arms["verifier-on"], arms["verifier-off"]
    table = Table(title="verifier on vs off — objective deltas (means over dataset)")
    table.add_column("metric")
    table.add_column("verifier ON", justify="right")
    table.add_column("verifier OFF", justify="right")

    def row(metric: str, f):
        table.add_row(metric, f"{f(on):.2f}", f"{f(off):.2f}")

    row("citation coverage", _mean_citation_coverage)
    row("cost / run ($)", lambda arm: mean(o.record.cost_estimate_usd for o in arm) if arm else 0.0)
    row("duration / run (s)", lambda arm: mean(sum(o.record.timings.values()) for o in arm) if arm else 0.0)
    row("claims / run", lambda arm: mean(len(o.record.claims) for o in arm) if arm else 0.0)
    console.print(table)

    if on:
        judged = [v for o in on for v in o.record.verdicts if v.verdict != "unverifiable"]
        unverifiable = [v for o in on for v in o.record.verdicts if v.verdict == "unverifiable"]
        total = sum(len(o.record.verdicts) for o in on)
        if judged:
            supported = sum(1 for v in judged if v.verdict == "supported")
            console.print(
                f"\n[bold]verifier-on only:[/bold] {supported}/{len(judged)} judged claims supported "
                f"({supported / len(judged):.0%}); {len(unverifiable)}/{total} unverifiable"
            )
    console.print(
        "\n[dim]Interpretation template: verification cost +$X/run and +Ys latency, and "
        "removed N source-contradicted claims that would otherwise have shipped; the "
        "unverifiable rate of Z% bounds how much of the web the verifier can actually check. "
        "Copy the numbers into README + JOURNAL.[/dim]"
    )


if __name__ == "__main__":
    asyncio.run(main())
