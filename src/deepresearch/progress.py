"""Typed progress events emitted by the orchestrator.

The orchestrator is UI-agnostic: it calls an ``on_event`` callback with these frozen
dataclasses. Phase 1 renders them as plain lines; the Rich Live UI arrives in Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    pass


@dataclass(frozen=True)
class PlanReady(ProgressEvent):
    n_sub_questions: int
    done_criteria: tuple[str, ...]


@dataclass(frozen=True)
class WaveStarted(ProgressEvent):
    wave: int
    n_questions: int


@dataclass(frozen=True)
class ResearcherStarted(ProgressEvent):
    sub_question_id: str
    question: str


@dataclass(frozen=True)
class ResearcherFinished(ProgressEvent):
    sub_question_id: str
    claims_found: int


@dataclass(frozen=True)
class ResearcherFailed(ProgressEvent):
    sub_question_id: str
    reason: str


@dataclass(frozen=True)
class GapResult(ProgressEvent):
    saturated: bool
    n_follow_ups: int


@dataclass(frozen=True)
class VerificationProgress(ProgressEvent):
    done: int
    total: int
    pass_rate: float


@dataclass(frozen=True)
class SynthesisStage(ProgressEvent):
    stage: str  # "outline" | "section"
    index: int = 0
    total: int = 0


@dataclass(frozen=True)
class CostUpdate(ProgressEvent):
    estimate_usd: float
    cap_usd: float


EventCallback = "Callable[[ProgressEvent], None]"
