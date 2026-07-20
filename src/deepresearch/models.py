"""The shared domain vocabulary. Every pipeline boundary speaks these types.

Count bounds that depend on the depth profile (e.g. sub-question counts) are enforced in
agent output validators, not here — the models stay profile-agnostic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
VerdictKind = Literal["supported", "partial", "unsupported", "unverifiable"]
CoverageStatus = Literal["covered", "partial", "uncovered"]


# --- planning ---------------------------------------------------------------


class PlannedSubQuestion(BaseModel):
    """A sub-question as emitted by the planner/gap analyst (ids are assigned by code)."""

    question: str = Field(description="A self-contained, searchable sub-question.")
    rationale: str = Field(description="Why answering this is necessary for the main question.")


class ResearchPlan(BaseModel):
    sub_questions: list[PlannedSubQuestion]
    done_criteria: list[str] = Field(
        description="Observable criteria for when the research question counts as answered."
    )


class SubQuestion(BaseModel):
    """A planned sub-question after the orchestrator assigned identity and wave."""

    id: str  # "sq-01", numbering continues across waves
    question: str
    rationale: str
    wave: int


class ClarifyingQuestions(BaseModel):
    """Clarifier output: questions whose answers would change the research plan."""

    questions: list[str] = Field(
        default_factory=list,
        description="0-3 clarifying questions; empty when the query is unambiguous.",
    )


# --- research ---------------------------------------------------------------


class RawClaim(BaseModel):
    """One atomic, checkable assertion extracted from a web source."""

    statement: str = Field(description="A single factual assertion, checkable against the source.")
    supporting_quote: str = Field(description="Verbatim quote from the source backing the statement.")
    source_url: str
    source_title: str
    confidence: Confidence


class Findings(BaseModel):
    """Researcher output for one sub-question."""

    claims: list[RawClaim]
    search_queries_used: list[str] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Dead ends, paywalls, ambiguity, or context the gap analyst should know.",
    )
    summary: str = Field(
        default="",
        description="2-4 sentences: what you established for your sub-question and what you could not.",
    )


class Claim(RawClaim):
    """A raw claim enriched by the orchestrator with identity and provenance."""

    id: str  # "c-001"
    sub_question_id: str
    wave: int
    date_accessed: str  # UTC date, set by code — never by the model
    corroborations: int = 0  # duplicate sightings folded into this claim


# --- gap analysis -----------------------------------------------------------


class SubQuestionCoverage(BaseModel):
    sub_question_id: str
    status: CoverageStatus
    gap_description: str = ""


class GapAnalysis(BaseModel):
    saturated: bool = Field(description="True when further research would not change the answer.")
    coverage: list[SubQuestionCoverage] = Field(default_factory=list)
    follow_up_questions: list[PlannedSubQuestion] = Field(
        default_factory=list,
        description="New sub-questions for the next wave; empty when saturated.",
    )


# --- verification -----------------------------------------------------------


class Verdict(BaseModel):
    claim_id: str
    verdict: VerdictKind
    reasoning: str = ""


class SourceVerification(BaseModel):
    """Verifier output for one source URL and every claim citing it."""

    source_url: str
    fetch_ok: bool
    verdicts: list[Verdict]


# --- synthesis --------------------------------------------------------------


class OutlineSection(BaseModel):
    title: str
    goal: str = Field(description="What this section must establish for the reader.")
    claim_ids: list[str]


class Outline(BaseModel):
    title: str
    tldr: str
    sections: list[OutlineSection]


class SectionText(BaseModel):
    markdown: str = Field(description="Section body in Markdown, citing claims as [n].")


class Critique(BaseModel):
    """Critic verdict on a synthesized draft, graded against the plan's acceptance criteria."""

    verdict: Literal["ship", "revise"]
    issues: list[str] = Field(default_factory=list, description="Concrete problems, one per entry.")
    guidance: str = Field(default="", description="Actionable instructions the reviser applies verbatim.")


# --- run record (the audit trail evals consume) ------------------------------


class FailedSubQuestion(BaseModel):
    sub_question_id: str
    error_class: str
    message: str


class RoleUsage(BaseModel):
    model: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class RunRecord(BaseModel):
    """Ground-truth artifact of a research run — written to run.json, read by evals."""

    run_id: str
    query: str
    clarified_query: str | None = None  # query + user clarifications, when --interactive asked any
    profile: str
    routing: str
    logfire_trace_id: str | None = None  # hex trace id → jump to this run's trace in Logfire
    models_used: dict[str, str] = Field(default_factory=dict)  # role -> resolved model string
    plan: ResearchPlan | None = None
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    failed_sub_questions: list[FailedSubQuestion] = Field(default_factory=list)
    usage: dict[str, RoleUsage] = Field(default_factory=dict)  # per role
    searches_used: int = 0
    cost_estimate_usd: float = 0.0
    timings: dict[str, float] = Field(default_factory=dict)  # phase -> seconds
    waves_run: int = 0
    saturated: bool = False
    synthesis_ok: bool = True
    critique_verdict: str | None = None  # final ship|revise
    critique_iterations: int = 0  # number of revise passes performed
    critique_issues: list[str] = Field(default_factory=list)  # issues from the final critique
    final_coverage: list[SubQuestionCoverage] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    report_path: str | None = None


class Checkpoint(BaseModel):
    """Resume point written to checkpoint.json after each completed pipeline stage.

    ``record`` doubles as the state payload (plan, claims, verdicts, usage, searches all live
    there already) — only the orchestrator working state that RunRecord does not carry is
    stored alongside. A finished run has no checkpoint (deleted on successful report write).
    """

    stage: Literal["planned", "wave_done", "verified"]
    record: RunRecord
    queue: list[SubQuestion] = Field(
        default_factory=list,
        description="Sub-questions for the next unlaunched wave; empty when the wave loop is done.",
    )
    seen_questions: list[str] = Field(default_factory=list)  # normalized dedup keys
    notes_by_sq: dict[str, str] = Field(default_factory=dict)
    summaries_by_sq: dict[str, str] = Field(default_factory=dict)
    wave_costs: list[float] = Field(default_factory=list)
