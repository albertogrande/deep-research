"""HITL tests: clarification answers reach the planner, the plan gate sits at the spend
boundary, and non-interactive runs never touch the clarifier."""

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from deepresearch.agents.clarifier import clarifier_agent
from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.config import Settings
from deepresearch.digest import clarified_query
from deepresearch.models import PlannedSubQuestion, ResearchPlan
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS, PLAN_ARGS

QUERY = "What should I know about Python?"


class RecordingHooks:
    """InteractionHooks double that records the call order and can edit the plan."""

    def __init__(self, answers: list[str] | None = None, add_question: str | None = None):
        self.answers = answers or []
        self.add_question = add_question
        self.calls: list[str] = []
        self.reviewed_plan: ResearchPlan | None = None

    async def clarify(self, questions: list[str]) -> list[str]:
        self.calls.append("clarify")
        return self.answers[: len(questions)]

    async def review_plan(self, plan: ResearchPlan) -> ResearchPlan:
        self.calls.append("review")
        if self.add_question:
            plan = ResearchPlan(
                sub_questions=[
                    *plan.sub_questions,
                    PlannedSubQuestion(question=self.add_question, rationale="added by user"),
                ],
                done_criteria=plan.done_criteria,
            )
        self.reviewed_plan = plan
        return plan


def clarifier_model(questions: list[str]) -> TestModel:
    return TestModel(custom_output_args={"questions": questions})


def test_clarified_query_folds_answers_and_skips_blanks():
    out = clarified_query("Q?", [("Which region?", "Europe"), ("Timeframe?", "  ")])
    assert "Q?" in out and "Clarifications from the user:" in out
    assert "A: Europe" in out
    assert "Timeframe?" not in out  # unanswered -> dropped
    assert clarified_query("Q?", [("Which region?", "")]) == "Q?"


async def test_clarification_reaches_planner_and_review_gates_spend(tmp_path):
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))
    hooks = RecordingHooks(answers=["For data engineering work"], add_question="What changed in 3.13?")
    planner_prompts: list[str] = []

    def recording_planner(messages, info: AgentInfo) -> ModelResponse:
        planner_prompts.append(
            "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, PLAN_ARGS)])

    def gated_researcher(messages, info: AgentInfo) -> ModelResponse:
        assert hooks.reviewed_plan is not None, "researcher ran before the plan was reviewed"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, FINDINGS_ARGS)])

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        clarifier_agent.override(model=clarifier_model(["What is this Python knowledge for?"])),
        planner_agent.override(model=FunctionModel(recording_planner)),
        r_agent.override(model=FunctionModel(gated_researcher), native_tools=[]),
    ):
        result = await run_research(QUERY, settings, interaction=hooks)

    record = result.record
    assert hooks.calls == ["clarify", "review"]  # clarify before planning, review right after
    assert "A: For data engineering work" in planner_prompts[0]  # answers reached the planner
    assert record.clarified_query is not None and "For data engineering work" in record.clarified_query
    # The user-added question got an id through the normal dedup/assignment path and was researched.
    added = [sq for sq in record.sub_questions if sq.question == "What changed in 3.13?"]
    assert len(added) == 1 and added[0].id == "sq-03"
    assert record.plan is not None and len(record.plan.sub_questions) == 3


async def test_no_clarifying_questions_skips_the_prompt(tmp_path, planner_model, researcher_model):
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))
    hooks = RecordingHooks()

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        clarifier_agent.override(model=clarifier_model([])),
        planner_agent.override(model=planner_model),
        r_agent.override(model=researcher_model, native_tools=[]),
    ):
        result = await run_research(QUERY, settings, interaction=hooks)

    assert hooks.calls == ["review"]  # no questions -> no clarify() prompt; the gate still ran
    assert result.record.clarified_query is None


async def test_non_interactive_run_never_calls_the_clarifier(tmp_path, planner_model, researcher_model):
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))

    def exploding_clarifier(messages, info: AgentInfo) -> ModelResponse:  # pragma: no cover
        raise AssertionError("clarifier must not run without interaction hooks")

    r_agent = researcher_agent(settings.prof.searches_per_researcher)
    with (
        clarifier_agent.override(model=FunctionModel(exploding_clarifier)),
        planner_agent.override(model=planner_model),
        r_agent.override(model=researcher_model, native_tools=[]),
    ):
        result = await run_research(QUERY, settings)

    assert result.record.clarified_query is None
