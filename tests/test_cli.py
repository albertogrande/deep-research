"""CLI exit-code contract: 0 ok · 1 fatal · 2 synthesis fallback · 3 spend refusal."""

import json

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.agents.synthesizer import outline_agent, section_agent
from deepresearch.agents.verifier import verifier_agent
from deepresearch.cli import app
from tests.conftest import FINDINGS_ARGS, PLAN_ARGS

runner = CliRunner()


def _outline(messages, info: AgentInfo) -> ModelResponse:
    args = {
        "title": "T",
        "tldr": "Answer.",
        "sections": [{"title": "S1", "goal": "g", "claim_ids": ["c-001", "c-002"]}],
    }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def _section(messages, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"markdown": "Body [1]."})])


def _verifier(messages, info: AgentInfo) -> ModelResponse:
    prompt = "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
    ids = [f"c-{i:03d}" for i in range(1, 10) if f"c-{i:03d}" in prompt]
    args = {
        "source_url": "https://en.wikipedia.org/wiki/Paris",
        "fetch_ok": True,
        "verdicts": [{"claim_id": cid, "verdict": "supported", "reasoning": "x"} for cid in ids],
    }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def _happy_overrides(tmp_path):
    r_agent = researcher_agent(3)  # quick profile: 3 searches per researcher
    return (
        planner_agent.override(model=TestModel(custom_output_args=PLAN_ARGS)),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(_verifier), native_tools=[]),
        outline_agent.override(model=FunctionModel(_outline)),
        section_agent.override(model=FunctionModel(_section)),
    )


def _invoke(tmp_path, *extra_args):
    args = ["What is the capital of France?", "-d", "quick", "-o", str(tmp_path / "runs"), *extra_args]
    return runner.invoke(app, args)


def test_exit_0_and_summary_on_success(tmp_path):
    o = _happy_overrides(tmp_path)
    with o[0], o[1], o[2], o[3], o[4]:
        result = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert "supported: 2" in result.output
    assert "report" in result.output


def test_json_output_is_parseable(tmp_path):
    o = _happy_overrides(tmp_path)
    with o[0], o[1], o[2], o[3], o[4]:
        result = _invoke(tmp_path, "--json")
    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    assert record["query"] == "What is the capital of France?"
    assert len(record["claims"]) == 2


def test_exit_2_on_synthesis_fallback(tmp_path):
    def crashing_outline(messages, info: AgentInfo) -> ModelResponse:
        raise RuntimeError("synth boom")

    o = _happy_overrides(tmp_path)
    with o[0], o[1], o[2], outline_agent.override(model=FunctionModel(crashing_outline)):
        result = _invoke(tmp_path)
    assert result.exit_code == 2, result.output


def test_exit_1_on_planner_failure(tmp_path):
    def broken_planner(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("not a plan")])

    with planner_agent.override(model=FunctionModel(broken_planner)):
        result = _invoke(tmp_path)
    assert result.exit_code == 1, result.output
    assert "fatal" in result.output


def test_exit_3_on_spend_refusal(tmp_path):
    from pydantic_ai.exceptions import ModelHTTPError

    def refused(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=402, model_name="test", body="spend cap exceeded")

    o = _happy_overrides(tmp_path)
    r_agent = researcher_agent(3)
    with o[0], r_agent.override(model=FunctionModel(refused), native_tools=[]):
        result = _invoke(tmp_path)
    assert result.exit_code == 3, result.output
    assert "spend" in result.output
