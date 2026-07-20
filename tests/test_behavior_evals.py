"""Behavioral evals asserted OFFLINE via OTel spans: the pipeline's *shape* (plan ran,
waves bounded by profile, verification present, no clarify without --interactive) checked
from the span tree pydantic-evals captures around each case. TestModel-scripted — no API
calls, no cost — so unlike the live eval suite these run in CI."""

from pydantic_ai.models.test import TestModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import HasMatchingSpan
from pydantic_evals.otel.span_tree import SpanQuery

from deepresearch.agents.gap_analyst import gap_analyst_agent
from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.agents.verifier import verifier_agent
from deepresearch.config import Settings
from deepresearch.orchestrator import run_research
from deepresearch.telemetry import setup_telemetry
from tests.conftest import FINDINGS_ARGS, PLAN_ARGS

VERIFIER_ARGS = {
    "source_url": "https://en.wikipedia.org/wiki/Paris",
    "fetch_ok": False,
    "verdicts": [],
}


def _scripted_task(settings: Settings):
    async def task(question: str) -> str:
        r_agent = researcher_agent(settings.prof.searches_per_researcher)
        with (
            planner_agent.override(model=TestModel(custom_output_args=PLAN_ARGS)),
            r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
            gap_analyst_agent.override(model=TestModel(custom_output_args={"saturated": True})),
            verifier_agent.override(model=TestModel(custom_output_args=VERIFIER_ARGS), native_tools=[]),
        ):
            result = await run_research(question, settings)
        return result.record.run_id

    return task


PIPELINE_SHAPE_EVALUATORS = [
    HasMatchingSpan(query=SpanQuery(name_equals="plan"), evaluation_name="planned"),
    HasMatchingSpan(query=SpanQuery(name_equals="wave {wave}"), evaluation_name="wave_ran"),
    HasMatchingSpan(query=SpanQuery(name_equals="verification"), evaluation_name="verified"),
    # quick profile has max_waves=1: a wave-2 span would mean the loop ignored its bound.
    HasMatchingSpan(
        query=SpanQuery(
            name_equals="research run",
            no_descendant_has=SpanQuery(name_equals="wave {wave}", has_attributes={"wave": 2}),
        ),
        evaluation_name="waves_bounded",
    ),
    # No interaction hooks were passed, so the clarify stage must never have run.
    HasMatchingSpan(
        query=SpanQuery(
            name_equals="research run",
            no_descendant_has=SpanQuery(name_equals="clarify"),
        ),
        evaluation_name="no_clarify_without_interactive",
    ),
]


async def test_pipeline_shape_via_spans(tmp_path):
    # Span capture needs logfire configured BEFORE dataset.evaluate sets up its recording
    # context — run_research calling it mid-task is too late. LOGFIRE_TOKEN is stripped by
    # the conftest fixture, so nothing leaves the process.
    setup_telemetry()
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))
    dataset = Dataset(
        name="pipeline-shape",
        cases=[Case(name="scripted-run", inputs="What is the capital of France?")],
        evaluators=PIPELINE_SHAPE_EVALUATORS,
    )
    report = await dataset.evaluate(_scripted_task(settings), name="pipeline-shape-offline")

    (case,) = report.cases
    outcomes = {name: result.value for name, result in case.assertions.items()}
    assert outcomes == {
        "planned": True,
        "wave_ran": True,
        "verified": True,
        "waves_bounded": True,
        "no_clarify_without_interactive": True,
    }
