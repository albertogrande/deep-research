import json

from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS

models.ALLOW_MODEL_REQUESTS = False  # belt & braces: no test may hit a real provider


async def test_single_wave_run(settings, planner_model, researcher_model):
    settings = settings.model_copy(update={"verify": False})  # wave plumbing only
    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        # TestModel rejects agents carrying server-side tools -> strip them via native_tools=[]
        r_agent.override(model=researcher_model, native_tools=[]),
    ):
        result = await run_research("What is the capital of France?", settings)

    record = result.record
    assert [sq.id for sq in record.sub_questions] == ["sq-01", "sq-02"]
    # 2 researchers × 2 scripted claims, minus dedup (identical scripted output collides)
    assert len(record.claims) == 2
    assert record.claims[0].id == "c-001"
    assert record.claims[0].date_accessed  # stamped by code
    assert record.waves_run == 1
    assert not record.failed_sub_questions
    assert set(record.usage) == {"planner", "researcher"}
    assert record.usage["researcher"].requests >= 2

    run_json = json.loads((result.run_dir / "run.json").read_text())
    assert run_json["run_id"] == record.run_id
    assert run_json["models_used"]["researcher"].startswith("gateway/anthropic:")


async def test_one_researcher_failure_does_not_kill_run(settings, planner_model):
    """First sub-question's researcher blows up; the run completes with the other's claims."""

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(
            str(getattr(part, "content", "")) for m in messages for part in getattr(m, "parts", [])
        )
        if "sq-01" in prompt:
            raise RuntimeError("boom: simulated researcher crash")
        output_tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(output_tool.name, FINDINGS_ARGS)])

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=FunctionModel(scripted), native_tools=[]),
    ):
        result = await run_research("What is the capital of France?", settings)

    record = result.record
    assert [f.sub_question_id for f in record.failed_sub_questions] == ["sq-01"]
    assert record.failed_sub_questions[0].error_class == "RuntimeError"
    assert len(record.claims) == 2  # sq-02 still delivered
    assert (result.run_dir / "run.json").exists()


async def test_run_json_flushed_even_when_planner_fails(settings):
    def broken_planner(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("not a plan")])

    with planner_agent.override(model=FunctionModel(broken_planner)):
        try:
            await run_research("anything", settings)
        except Exception:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected planner failure to propagate")

    runs = list((settings_output_dir(settings)).glob("*/run.json"))
    assert len(runs) == 1, "partial run.json must be flushed on fatal errors"
    data = json.loads(runs[0].read_text())
    assert data["claims"] == []


def settings_output_dir(settings):
    from pathlib import Path

    return Path(settings.output_dir)
