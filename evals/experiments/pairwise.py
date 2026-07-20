"""DeepConsult-style pairwise comparison: which of two arms writes the better report?

Two modes:
  uv run python -m evals.experiments.pairwise runs-evals/verify-on runs-evals/verify-off
      Compare existing run artifacts (report.md + run.json pairs matched by query). $ = judge
      calls only (~2 x cases x $0.01).
  uv run python -m evals.experiments.pairwise --live --arm-a depth=quick --arm-b depth=standard
      Run the eval dataset through two live configs first (costs two full eval runs), then
      judge. Arm specs are comma-separated key=value Settings overrides.

Every pair is judged twice with positions swapped; a win only counts when both orders agree,
otherwise it is a tie — position bias is the classic pairwise-judge failure mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent
from rich.console import Console
from rich.table import Table

from deepresearch.config import Settings
from deepresearch.telemetry import setup_telemetry

from ..common import EVAL_RUNS_DIR, EvalOutput, judge_model, make_task
from ..run_evals import load_dataset

console = Console()


class PairVerdict(BaseModel):
    winner: str  # "A" | "B" | "tie"
    reasoning: str = ""


pairwise_judge: Agent[None, PairVerdict] = Agent(
    output_type=PairVerdict,
    instructions=(
        "You compare two research reports answering the same question. Judge which better "
        "serves a demanding reader: directly answers the question, evidence-dense and "
        "well-cited, covers opposing evidence, separates fact from interpretation, no "
        "padding. Ignore length per se and ignore which is first. Return winner 'A', 'B', "
        "or 'tie' (tie only when genuinely comparable)."
    ),
    retries=2,
)


def load_arm_from_dir(arm_dir: Path) -> dict[str, str]:
    """Map query -> report markdown from a directory of run dirs (report.md + run.json)."""
    reports: dict[str, str] = {}
    for run_json in sorted(arm_dir.glob("**/run.json")):
        report_path = run_json.parent / "report.md"
        if not report_path.exists():
            continue
        query = json.loads(run_json.read_text(encoding="utf-8"))["query"]
        reports[query] = report_path.read_text(encoding="utf-8")
    return reports


def parse_arm_spec(spec: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        key, _, value = part.partition("=")
        overrides["profile" if key == "depth" else key] = value
    return overrides


async def run_live_arm(name: str, spec: str, max_concurrency: int) -> dict[str, str]:
    settings = Settings(output_dir=str(EVAL_RUNS_DIR / f"pairwise-{name}"), **parse_arm_spec(spec))
    outputs: list[EvalOutput] = []
    inner = make_task(settings)

    async def task(question: str) -> EvalOutput:  # the _capture_task pattern from verifier_ab
        out = await inner(question)
        outputs.append(out)
        return out

    dataset = load_dataset()
    console.rule(f"[bold]running arm {name}: {spec}")
    await dataset.evaluate(task, name=f"pairwise-{name}", max_concurrency=max_concurrency)
    return {o.record.query: o.report_markdown for o in outputs if o.report_markdown}


async def judge_pair(question: str, report_a: str, report_b: str, model: str) -> str:
    """Position-swapped double judgment; disagreement between orders counts as a tie."""

    async def one(first: str, second: str) -> str:
        prompt = f"QUESTION:\n{question}\n\n=== REPORT A ===\n{first}\n\n=== REPORT B ===\n{second}"
        return (await pairwise_judge.run(prompt, model=model)).output.winner

    forward = await one(report_a, report_b)
    backward = await one(report_b, report_a)
    backward_flipped = {"A": "B", "B": "A"}.get(backward, "tie")
    return forward if forward == backward_flipped else "tie"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="*", help="two arm directories (artifact mode)")
    parser.add_argument("--live", action="store_true", help="run two live arms first")
    parser.add_argument("--arm-a", default="depth=quick", help="live arm A Settings overrides")
    parser.add_argument("--arm-b", default="depth=standard", help="live arm B Settings overrides")
    parser.add_argument("--max-concurrency", type=int, default=2)
    args = parser.parse_args()

    setup_telemetry()
    if args.live:
        label_a, label_b = args.arm_a, args.arm_b
        arm_a = await run_live_arm("a", args.arm_a, args.max_concurrency)
        arm_b = await run_live_arm("b", args.arm_b, args.max_concurrency)
    elif len(args.dirs) == 2:
        label_a, label_b = args.dirs
        arm_a = load_arm_from_dir(Path(args.dirs[0]))
        arm_b = load_arm_from_dir(Path(args.dirs[1]))
    else:
        parser.error("pass two arm directories, or --live with --arm-a/--arm-b")
        return

    shared = sorted(set(arm_a) & set(arm_b))
    if not shared:
        console.print("[red]no queries in common between the arms — nothing to judge[/red]")
        return

    model = judge_model(Settings())
    tally = {"A": 0, "B": 0, "tie": 0}
    for question in shared:
        outcome = await judge_pair(question, arm_a[question], arm_b[question], model)
        tally[outcome] += 1
        console.print(f"[dim]{outcome:>3}[/dim]  {question[:80]}")

    table = Table(title="pairwise win/tie/loss (position-swapped, disagreement = tie)")
    table.add_column("arm")
    table.add_column("wins", justify="right")
    table.add_row(f"A: {label_a}", str(tally["A"]))
    table.add_row(f"B: {label_b}", str(tally["B"]))
    table.add_row("ties", str(tally["tie"]))
    console.print(table)
    console.print(f"[dim]{len(shared)} shared question(s) judged; judge: {model}[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
