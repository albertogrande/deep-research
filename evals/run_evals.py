"""Run the eval suite (LIVE: costs ~$2.50 at quick depth for 8 cases — never wire into CI;
--depth standard roughly doubles that, and judges add ~$0.50).

    uv run python -m evals.run_evals [--no-verify] [--no-judges] [--depth quick|standard]
                                     [--max-concurrency N]

Experiments land in Logfire automatically when LOGFIRE_TOKEN is set.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydantic_evals import Dataset
from pydantic_evals.evaluators import LLMJudge, MaxDuration

from deepresearch.telemetry import setup_telemetry

from .common import EvalMeta, EvalOutput, eval_settings, install_default_judge, judge_model, make_task
from .evaluators import (
    CitationCoverage,
    CitationDensity,
    CitationIntegrity,
    UnsupportedLeakage,
    UnverifiableRate,
    URLResolution,
    VerifiedClaimRate,
)
from .judges import CitationSupport, race_judges

DATASET_PATH = Path(__file__).parent / "dataset.yaml"


def load_dataset() -> Dataset[str, EvalOutput, EvalMeta]:
    return Dataset[str, EvalOutput, EvalMeta].from_file(DATASET_PATH)


def objective_evaluators() -> list:
    return [
        CitationCoverage(),
        CitationDensity(),
        CitationIntegrity(),
        VerifiedClaimRate(),
        UnverifiableRate(),
        UnsupportedLeakage(),
        URLResolution(),
        MaxDuration(seconds=420),
    ]


def judge_evaluators(model: str) -> list:
    # Judge model is explicit on every LLMJudge: the library default is an OpenAI model
    # and this project has no OpenAI key (see JOURNAL.md).
    return [
        LLMJudge(
            rubric=(
                "The report directly answers the research question, covers its major facets, "
                "and explicitly states what remains uncertain."
            ),
            model=model,
            include_input=True,
            score={"evaluation_name": "completeness"},
            assertion=False,
        ),
        LLMJudge(
            rubric=(
                "Specific facts (numbers, dates, names) are attributed to numbered citations; "
                "no confident factual claim appears without a citation."
            ),
            model=model,
            include_input=False,
            score={"evaluation_name": "faithfulness"},
            assertion=False,
        ),
        LLMJudge(
            rubric=(
                "If the question contains a false or dubious premise, the report explicitly "
                "corrects it; for contested topics, evidence on multiple sides is presented "
                "without false balance. Otherwise judge neutrally as satisfied."
            ),
            model=model,
            include_input=True,
            score={"evaluation_name": "premise_handling"},
            assertion=False,
        ),
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-verify", action="store_true", help="run with claim verification off")
    parser.add_argument("--no-judges", action="store_true", help="objective evaluators only (cheaper)")
    parser.add_argument("--depth", default="quick", choices=["quick", "standard", "deep"])
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--name", default=None, help="experiment name shown in Logfire")
    args = parser.parse_args()

    setup_telemetry()
    subdir = ("verify-on" if not args.no_verify else "verify-off") + (
        f"-{args.depth}" if args.depth != "quick" else ""
    )
    settings = eval_settings(verify=not args.no_verify, out_subdir=subdir, profile=args.depth)
    install_default_judge(settings)

    dataset = load_dataset()
    dataset.evaluators.extend(objective_evaluators())
    if not args.no_judges:
        model = judge_model(settings)
        dataset.evaluators.extend(judge_evaluators(model))
        dataset.evaluators.extend(race_judges(model))
        dataset.evaluators.append(CitationSupport(model=model))

    name = args.name or (
        "deepresearch-evals"
        + ("-noverify" if args.no_verify else "")
        + (f"-{args.depth}" if args.depth != "quick" else "")
    )
    report = await dataset.evaluate(make_task(settings), name=name, max_concurrency=args.max_concurrency)
    report.print(include_input=True, include_output=False, include_durations=True)


if __name__ == "__main__":
    asyncio.run(main())
