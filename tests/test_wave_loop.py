"""Wave-loop policy tests: saturation, max-waves, dedup, budget, transient retry.

All scripted via TestModel/FunctionModel — standard profile (max_waves=2) unless noted.
"""

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

import deepresearch.orchestrator as orch
from deepresearch.agents.gap_analyst import gap_analyst_agent
from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.config import Settings
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS

QUERY = "What is the capital of France?"


@pytest.fixture
def std_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, profile="standard", output_dir=str(tmp_path / "runs"))


@pytest.fixture
def std_plan_args() -> dict:
    # standard profile requires 3-5 sub-questions
    return {
        "sub_questions": [
            {"question": "What is the capital of France?", "rationale": "core"},
            {"question": "What is the population of Paris?", "rationale": "support"},
            {"question": "When did Paris become the capital?", "rationale": "history"},
        ],
        "done_criteria": ["capital named"],
    }


def gap_model(*, saturated: bool, follow_ups: list[dict] | None = None) -> TestModel:
    return TestModel(
        custom_output_args={
            "saturated": saturated,
            "coverage": [{"sub_question_id": "sq-01", "status": "covered", "gap_description": ""}],
            "follow_up_questions": follow_ups or [],
        }
    )


def researcher_stub() -> TestModel:
    return TestModel(custom_output_args=FINDINGS_ARGS)


async def test_saturation_stops_after_wave_1(std_settings, std_plan_args):
    r_agent = researcher_agent(std_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=researcher_stub(), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=True)),
    ):
        result = await run_research(QUERY, std_settings)

    record = result.record
    assert record.waves_run == 1
    assert record.saturated is True
    assert record.final_coverage and record.final_coverage[0].status == "covered"
    assert all(sq.wave == 1 for sq in record.sub_questions)


async def test_follow_ups_run_second_wave_then_max_waves_stops(std_settings, std_plan_args):
    follow_ups = [
        {"question": "How has the population of Paris changed since 2000?", "rationale": "gap"},
        {"question": "What is the metro-area population of Paris?", "rationale": "gap"},
    ]
    r_agent = researcher_agent(std_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=researcher_stub(), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=False, follow_ups=follow_ups)),
    ):
        result = await run_research(QUERY, std_settings)

    record = result.record
    assert record.waves_run == 2
    assert record.saturated is False
    wave2 = [sq for sq in record.sub_questions if sq.wave == 2]
    assert [sq.id for sq in wave2] == ["sq-04", "sq-05"]  # numbering continues
    assert any("max_waves" in lim for lim in record.limitations)


async def test_repeated_follow_ups_are_deduped_and_loop_stops(std_settings, std_plan_args):
    # Gap analyst restates an already-asked question (modulo phrasing) -> dedup empties queue.
    follow_ups = [{"question": "what is the population of paris", "rationale": "dup"}]
    r_agent = researcher_agent(std_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=researcher_stub(), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=False, follow_ups=follow_ups)),
    ):
        result = await run_research(QUERY, std_settings)

    record = result.record
    assert record.waves_run == 1
    assert any("already-asked" in lim for lim in record.limitations)


async def test_budget_breach_stops_new_waves_but_run_completes(std_settings, std_plan_args):
    tight = std_settings.model_copy(update={"max_cost": 0.000001})

    def exploding_gap(messages, info: AgentInfo) -> ModelResponse:  # pragma: no cover
        raise AssertionError("gap analyst must not run once the budget is spent")

    r_agent = researcher_agent(tight.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=researcher_stub(), native_tools=[]),
        gap_analyst_agent.override(model=FunctionModel(exploding_gap)),
    ):
        result = await run_research(QUERY, tight)

    record = result.record
    assert record.waves_run == 1
    assert any("budget cap" in lim for lim in record.limitations)
    assert record.claims  # wave-1 claims survive


async def test_unaffordable_second_wave_is_skipped_predictively(std_settings, std_plan_args, monkeypatch):
    """Wave 1 measures at $0.60 of a $1.00 cap. Remaining $0.40 < 0.8 x $0.60, so wave 2 is
    never launched — the gate predicts the overshoot instead of catching it afterwards."""
    import deepresearch.deps as deps_mod

    def fake_estimate(self, ledger):
        # $0 before any researcher has run, $0.60 after — robust to how often the
        # orchestrator (checkpoints, cost events) asks in between.
        researcher = ledger.by_role.get("researcher")
        return 0.6 if researcher and researcher.requests else 0.0

    monkeypatch.setattr(deps_mod.Budget, "estimate", fake_estimate)
    settings = std_settings.model_copy(update={"max_cost": 1.0, "verify": False})
    follow_ups = [{"question": "How has the population changed since 2000?", "rationale": "gap"}]

    def exploding_researcher_wave2(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(
            str(getattr(part, "content", "")) for m in messages for part in getattr(m, "parts", [])
        )
        if "sq-04" in prompt:  # pragma: no cover
            raise AssertionError("wave 2 researcher must not run when unaffordable")
        output_tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(output_tool.name, FINDINGS_ARGS)])

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=FunctionModel(exploding_researcher_wave2), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=False, follow_ups=follow_ups)),
    ):
        result = await run_research(QUERY, settings)

    record = result.record
    assert record.waves_run == 1
    assert any("wave 2 skipped: remaining budget" in lim for lim in record.limitations)
    assert record.claims  # wave-1 work is kept


async def test_wave2_researchers_get_known_so_far_brief(std_settings, std_plan_args):
    follow_ups = [{"question": "How has the population changed since 2000?", "rationale": "gap"}]
    prompts_by_sq: dict[str, str] = {}

    def recording_researcher(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(
            str(getattr(part, "content", "")) for m in messages for part in getattr(m, "parts", [])
        )
        sq_id = next((f"sq-{i:02d}" for i in range(1, 9) if f"sq-{i:02d}" in prompt), "unknown")
        prompts_by_sq[sq_id] = prompt
        output_tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(output_tool.name, FINDINGS_ARGS)])

    r_agent = researcher_agent(std_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=FunctionModel(recording_researcher), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=False, follow_ups=follow_ups)),
    ):
        result = await run_research(QUERY, std_settings)

    assert result.record.waves_run == 2
    for sq_id in ("sq-01", "sq-02", "sq-03"):  # wave 1: isolated researchers
        assert "ALREADY ESTABLISHED" not in prompts_by_sq[sq_id]
    wave2_prompt = prompts_by_sq["sq-04"]
    assert "ALREADY ESTABLISHED" in wave2_prompt
    # The brief is built from the wave-1 researchers' own summaries.
    assert FINDINGS_ARGS["summary"] in wave2_prompt


async def test_transient_researcher_error_retried_once(std_settings, std_plan_args, monkeypatch):
    monkeypatch.setattr(orch, "TRANSIENT_RETRY_DELAY_S", 0)
    from pydantic_ai.exceptions import ModelHTTPError

    calls: dict[str, int] = {}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(
            str(getattr(part, "content", "")) for m in messages for part in getattr(m, "parts", [])
        )
        sq_id = next((f"sq-{i:02d}" for i in range(1, 9) if f"sq-{i:02d}" in prompt), "unknown")
        calls[sq_id] = calls.get(sq_id, 0) + 1
        if sq_id == "sq-01" and calls[sq_id] == 1:
            raise ModelHTTPError(status_code=529, model_name="test")  # overloaded -> transient
        output_tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(output_tool.name, FINDINGS_ARGS)])

    r_agent = researcher_agent(std_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=std_plan_args)),
        r_agent.override(model=FunctionModel(flaky), native_tools=[]),
        gap_analyst_agent.override(model=gap_model(saturated=True)),
    ):
        result = await run_research(QUERY, std_settings)

    record = result.record
    assert not record.failed_sub_questions  # retry rescued sq-01
    assert calls["sq-01"] == 2
    assert record.claims
