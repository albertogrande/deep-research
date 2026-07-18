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


class RunRecord(BaseModel):
    """Ground-truth artifact of a research run — written to run.json, read by evals."""

    run_id: str
    query: str
    profile: str
    routing: str
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
    limitations: list[str] = Field(default_factory=list)
    report_path: str | None = None
