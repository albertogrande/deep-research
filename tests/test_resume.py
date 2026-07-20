"""Resume/checkpoint tests: checkpoint survives interrupts, resume skips completed stages,
ledger restore keeps the original cap binding, claim numbering continues across a resume."""

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from deepresearch.agents.critic import critic_agent
from deepresearch.agents.gap_analyst import gap_analyst_agent
from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.agents.synthesizer import outline_agent, section_agent
from deepresearch.agents.verifier import verifier_agent
from deepresearch.artifacts import load_checkpoint, write_checkpoint
from deepresearch.config import Settings
from deepresearch.deps import Budget, UsageLedger
from deepresearch.models import (
    Checkpoint,
    Claim,
    PlannedSubQuestion,
    ResearchPlan,
    RoleUsage,
    RunRecord,
    SubQuestion,
)
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS, PLAN_ARGS
from tests.test_synthesis import critic_ship, outline_scripted, section_scripted, verifier_scripted

QUERY = "What is the capital of France?"


class _Interrupt(BaseException):
    """Stands in for Ctrl-C. A real KeyboardInterrupt aborts the asyncio loop itself (it is
    special-cased in Task.__step), so tests use a plain BaseException subclass — like KI it
    bypasses every `except Exception` degrade path, but it propagates through awaits."""


def _explode(name: str):
    def fn(messages, info: AgentInfo) -> ModelResponse:  # pragma: no cover
        raise AssertionError(f"{name} must not be called on this path")

    return fn


def test_ledger_restore_counts_prior_spend():
    settings = Settings(_env_file=None, profile="quick")
    ledger = UsageLedger()
    ledger.restore(
        {
            "researcher": RoleUsage(
                model="claude-haiku-4-5", requests=3, input_tokens=500_000, output_tokens=100_000
            )
        },
        searches=9,
    )
    estimate = Budget(max_cost_usd=1.0, settings=settings).estimate(ledger)
    assert estimate > 0.5  # restored tokens + searches price in, so the original cap binds
    assert ledger.searches == 9
    assert ledger.restored_searches == 9


async def test_interrupt_leaves_resumable_checkpoint(tmp_path):
    """Ctrl-C during gap analysis: run.json is flushed AND checkpoint.json marks the last
    completed stage (planned — the wave-1 queue), ready for --resume."""
    settings = Settings(_env_file=None, profile="standard", output_dir=str(tmp_path / "runs"))
    plan_args = {
        "sub_questions": [
            {"question": "What is the capital of France?", "rationale": "core"},
            {"question": "What is the population of Paris?", "rationale": "support"},
            {"question": "When did Paris become the capital?", "rationale": "history"},
        ],
        "done_criteria": ["capital named"],
    }

    def interrupted_gap(messages, info: AgentInfo) -> ModelResponse:
        raise _Interrupt

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=TestModel(custom_output_args=plan_args)),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        gap_analyst_agent.override(model=FunctionModel(interrupted_gap)),
    ):
        with pytest.raises(_Interrupt):
            await run_research(QUERY, settings)

    run_dir = next(Path(settings.output_dir).iterdir())
    assert (run_dir / "run.json").exists()  # audit trail flushed on interrupt
    cp = load_checkpoint(run_dir)
    assert cp.stage == "planned"
    assert [sq.id for sq in cp.queue] == ["sq-01", "sq-02", "sq-03"]
    assert cp.record.plan is not None


async def test_resume_after_synthesis_interrupt_skips_research_and_verification(tmp_path):
    """Interrupt during the critic pass -> checkpoint says 'verified'. Resume must go straight
    to synthesis: planner/researcher/verifier are exploding stubs and must never run."""
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))
    r_agent = researcher_agent(settings.prof.searches_per_researcher)

    def interrupted_critic(messages, info: AgentInfo) -> ModelResponse:
        raise _Interrupt

    with (
        planner_agent.override(model=TestModel(custom_output_args=PLAN_ARGS)),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001", "c-002"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=FunctionModel(interrupted_critic)),
    ):
        with pytest.raises(_Interrupt):
            await run_research(QUERY, settings)

    run_dir = next(Path(settings.output_dir).iterdir())
    assert load_checkpoint(run_dir).stage == "verified"

    with (
        planner_agent.override(model=FunctionModel(_explode("planner"))),
        r_agent.override(model=FunctionModel(_explode("researcher")), native_tools=[]),
        verifier_agent.override(model=FunctionModel(_explode("verifier")), native_tools=[]),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001", "c-002"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=critic_ship()),
    ):
        result = await run_research("", settings, resume_from=run_dir)

    record = result.record
    assert record.query == QUERY  # restored from the checkpoint, not the empty CLI arg
    assert record.synthesis_ok is True
    assert result.report_path is not None and result.report_path.exists()
    assert [c.id for c in record.claims] == ["c-001", "c-002"]
    assert record.verdicts  # verification results survived the resume
    assert not (run_dir / "checkpoint.json").exists()  # finished runs need no resume point


async def test_resume_mid_wave_continues_ids_and_gets_brief(tmp_path):
    """A hand-built 'wave_done' checkpoint with a queued wave-2 question: resume launches only
    that wave, the researcher sees the ALREADY ESTABLISHED brief, and claim ids continue."""
    run_dir = tmp_path / "runs" / "r-resume"
    run_dir.mkdir(parents=True)
    settings = Settings(_env_file=None, profile="standard", output_dir=str(tmp_path / "runs"))

    wave1_claims = [
        Claim(
            id=f"c-{i:03d}",
            sub_question_id="sq-01",
            wave=1,
            date_accessed="2026-07-19",
            statement=s,
            supporting_quote="q",
            source_url="https://en.wikipedia.org/wiki/Paris",
            source_title="Paris - Wikipedia",
            confidence="high",
        )
        for i, s in [(1, "Paris is the capital of France."), (2, "Paris has 2.1M inhabitants.")]
    ]
    sub_questions = [
        SubQuestion(id="sq-01", question="What is the capital of France?", rationale="core", wave=1),
        SubQuestion(id="sq-04", question="How did Paris grow since 2000?", rationale="gap", wave=2),
    ]
    record = RunRecord(
        run_id="r-resume",
        query=QUERY,
        profile="standard",
        routing="gateway",
        plan=ResearchPlan(
            sub_questions=[PlannedSubQuestion(question="What is the capital of France?", rationale="core")],
            done_criteria=["capital named"],
        ),
        sub_questions=sub_questions,
        claims=wave1_claims,
        usage={"researcher": RoleUsage(model="claude-haiku-4-5", requests=1, input_tokens=1000)},
        searches_used=5,
        waves_run=1,
    )
    write_checkpoint(
        Checkpoint(
            stage="wave_done",
            record=record,
            queue=[sub_questions[1]],
            seen_questions=["capital of france", "paris grow since 2000"],
            summaries_by_sq={"sq-01": "Paris established as the capital."},
            wave_costs=[0.01],
        ),
        run_dir,
    )

    prompts: list[str] = []

    def wave2_researcher(messages, info: AgentInfo) -> ModelResponse:
        prompts.append(
            "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
        )
        args = {
            "claims": [
                {
                    "statement": "Greater Paris grew to 13M people by 2020.",
                    "supporting_quote": "q",
                    "source_url": "https://insee.fr/stats",
                    "source_title": "INSEE",
                    "confidence": "medium",
                }
            ],
            "search_queries_used": [],
            "notes": "",
            "summary": "Growth established.",
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=FunctionModel(_explode("planner"))),
        r_agent.override(model=FunctionModel(wave2_researcher), native_tools=[]),
        verifier_agent.override(model=FunctionModel(verifier_scripted({})), native_tools=[]),
        outline_agent.override(model=FunctionModel(outline_scripted([["c-001", "c-002", "c-003"]]))),
        section_agent.override(model=FunctionModel(section_scripted())),
        critic_agent.override(model=critic_ship()),
    ):
        result = await run_research("", settings, resume_from=run_dir)

    record = result.record
    assert len(prompts) == 1 and "sq-04" in prompts[0]  # only the queued wave ran
    assert "ALREADY ESTABLISHED" in prompts[0]
    assert "Paris established as the capital." in prompts[0]  # brief built from restored summaries
    assert record.waves_run == 2
    assert [c.id for c in record.claims] == ["c-001", "c-002", "c-003"]  # numbering continued
    assert record.searches_used >= 5  # restored searches still counted
    assert result.report_path is not None
    assert not (run_dir / "checkpoint.json").exists()
