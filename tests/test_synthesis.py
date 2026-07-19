"""Synthesis tests: full report path, outline validation, unsupported exclusion, fallback."""

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from deepresearch.agents.critic import critic_agent
from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.agents.synthesizer import outline_agent, section_agent
from deepresearch.agents.verifier import verifier_agent
from deepresearch.config import Settings
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS

QUERY = "What is the capital of France?"


@pytest.fixture
def quick_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))


def _sniff_claim_ids(messages) -> list[str]:
    prompt = "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
    return [f"c-{i:03d}" for i in range(1, 20) if f"c-{i:03d}" in prompt]


def verifier_scripted(verdict_for: dict[str, str]):
    def fn(messages, info: AgentInfo) -> ModelResponse:
        ids = _sniff_claim_ids(messages)
        args = {
            "source_url": "https://en.wikipedia.org/wiki/Paris",
            "fetch_ok": True,
            "verdicts": [
                {"claim_id": cid, "verdict": verdict_for.get(cid, "supported"), "reasoning": "x"}
                for cid in ids
            ],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return fn


def outline_scripted(claim_ids_by_section: list[list[str]]):
    def fn(messages, info: AgentInfo) -> ModelResponse:
        args = {
            "title": "Paris: France's Capital",
            "tldr": "Paris is the capital of France.",
            "sections": [
                {"title": f"Section {i + 1}", "goal": "explain", "claim_ids": ids}
                for i, ids in enumerate(claim_ids_by_section)
            ],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return fn


def section_scripted():
    def fn(messages, info: AgentInfo) -> ModelResponse:
        prompt = "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
        n = "1" if "[1]" in prompt else "?"
        args = {"markdown": f"Paris is the capital of France [{n}]."}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return fn


def critic_ship() -> TestModel:
    return TestModel(custom_output_args={"verdict": "ship", "issues": [], "guidance": ""})


def critic_verdicts(sequence: list[str]):
    """FunctionModel that returns verdicts in order across successive critic calls."""
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        i = min(calls["n"], len(sequence) - 1)
        verdict = sequence[i]
        calls["n"] += 1
        args = {
            "verdict": verdict,
            "issues": [] if verdict == "ship" else ["redundant paragraph under Section 1"],
            "guidance": "" if verdict == "ship" else "delete the repeated sentence in Section 1",
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return fn


async def test_full_pipeline_produces_report(quick_settings, planner_model):
    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001"], ["c-002"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=critic_ship()),
    ):
        result = await run_research(QUERY, quick_settings)

    assert result.report_path is not None
    report = result.report_path.read_text()
    assert report.startswith("# Paris: France's Capital")
    assert "> **TL;DR**" in report
    assert "## Section 1" in report and "## Section 2" in report
    assert "[1]" in report  # inline citation from scripted section
    assert "## References" in report
    assert "en.wikipedia.org/wiki/Paris" in report
    assert result.record.synthesis_ok is True
    assert result.record.report_path == str(result.report_path)


async def test_outline_validator_rejects_unknown_claim_ids(quick_settings, planner_model):
    attempts = {"n": 0}

    def bad_then_good_outline(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        ids = [["c-999"]] if attempts["n"] == 1 else [["c-001", "c-002"]]
        return outline_scripted(ids)(messages, info)

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(bad_then_good_outline)),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=critic_ship()),
    ):
        result = await run_research(QUERY, quick_settings)

    assert attempts["n"] >= 2  # ModelRetry forced a second outline
    assert result.record.synthesis_ok is True


async def test_unsupported_claims_excluded_from_report_but_kept_in_record(quick_settings, planner_model):
    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(
            model=FunctionModel(verifier_scripted({"c-002": "unsupported"})), native_tools=[]
        ),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=critic_ship()),
    ):
        result = await run_research(QUERY, quick_settings)

    record = result.record
    assert {v.claim_id: v.verdict for v in record.verdicts}["c-002"] == "unsupported"  # audit trail
    assert any("contradicted" in lim for lim in record.limitations)
    report = result.report_path.read_text()
    assert "## Limitations" in report and "contradicted" in report


async def test_synthesis_failure_writes_claims_dump_fallback(quick_settings, planner_model):
    def crashing_outline(messages, info: AgentInfo) -> ModelResponse:
        raise RuntimeError("synth boom")

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(crashing_outline)),
    ):
        result = await run_research(QUERY, quick_settings)

    assert result.record.synthesis_ok is False
    assert result.report_path is not None
    report = result.report_path.read_text()
    assert report.startswith("# Research findings (unsynthesized)")
    assert "## References" in report  # sources still listed
    assert any("synthesis failed" in lim for lim in result.record.limitations)


def _revise_loop_overrides(quick_settings, planner_model, critic_model):
    """Common overrides for critic revise-loop tests; caller supplies the critic model."""
    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    return (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001"], ["c-002"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=FunctionModel(critic_model)),
    )


async def test_critic_revise_once_then_ship(quick_settings, planner_model):
    # First critique says revise, second says ship -> exactly one revise pass; two section rounds.
    section_calls = {"n": 0}

    def counting_section(messages, info: AgentInfo) -> ModelResponse:
        section_calls["n"] += 1
        return section_scripted()(messages, info)

    o = _revise_loop_overrides(quick_settings, planner_model, critic_verdicts(["revise", "ship"]))
    with o[0], o[1], o[2], o[3], section_agent.override(model=FunctionModel(counting_section)), o[5]:
        result = await run_research(QUERY, quick_settings)

    record = result.record
    assert record.critique_verdict == "ship"
    assert record.critique_iterations == 1  # one revise pass performed
    assert section_calls["n"] == 4  # 2 sections × (initial draft + 1 revise)
    assert result.report_path is not None


async def test_critic_revise_capped_at_max_iters(quick_settings, planner_model):
    # Critic never satisfied -> loop stops at MAX_REVISE_ITERS, ships last draft with verdict revise.
    from deepresearch.orchestrator import MAX_REVISE_ITERS

    o = _revise_loop_overrides(quick_settings, planner_model, critic_verdicts(["revise"]))
    with o[0], o[1], o[2], o[3], o[4], o[5]:
        result = await run_research(QUERY, quick_settings)

    record = result.record
    assert record.critique_verdict == "revise"  # never satisfied
    assert record.critique_iterations == MAX_REVISE_ITERS
    assert record.critique_issues  # last critique's issues recorded
    assert result.record.synthesis_ok is True  # capped, not failed — still a real report
