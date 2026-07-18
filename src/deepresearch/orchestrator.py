"""Pipeline orchestration: sequencing, concurrency, budget, and error policy live HERE.

Phase 1 scope: plan → one wave of parallel researchers → run.json. The wave loop, gap
analysis, verification, and synthesis stages arrive in Phases 2-4.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import logfire
from pydantic_ai import UsageLimits

from .agents.planner import planner_agent
from .agents.researcher import researcher_agent
from .artifacts import create_run_dir, new_run_id, write_run_record
from .config import Role, Settings, resolve_model
from .deps import Budget, Deps, UsageLedger
from .digest import assign_sub_question_ids, dedup_questions, enrich_and_dedup_claims
from .models import Claim, FailedSubQuestion, Findings, RunRecord, SubQuestion
from .progress import (
    CostUpdate,
    PlanReady,
    ProgressEvent,
    ResearcherFailed,
    ResearcherFinished,
    ResearcherStarted,
    WaveStarted,
)
from .telemetry import setup_telemetry

ROLES: tuple[Role, ...] = ("planner", "researcher", "gap_analyst", "verifier", "synthesizer")

PLANNER_LIMITS = UsageLimits(request_limit=5)
RESEARCHER_LIMITS = UsageLimits(request_limit=12, total_tokens_limit=120_000)


@dataclass
class RunResult:
    record: RunRecord
    run_dir: Path
    report_path: Path | None = None


def _researcher_prompt(main_query: str, sq: SubQuestion) -> str:
    return (
        f"Main research question (context only — do not research it directly):\n{main_query}\n\n"
        f"YOUR sub-question ({sq.id}):\n{sq.question}\n\n"
        f"Why it matters: {sq.rationale}"
    )


def _reconcile_searches(ledger: UsageLedger) -> None:
    """Replace the conservative per-run search estimate with real provider counters when
    the SDK surfaces them (usage.details keys containing 'web_search')."""
    total = 0
    for usage in ledger.by_role.values():
        for key, value in (usage.details or {}).items():
            if "web_search" in key:
                total += value
    if total:
        ledger.searches = total


async def run_research(
    query: str,
    settings: Settings,
    on_event: Callable[[ProgressEvent], None] | None = None,
) -> RunResult:
    emit = on_event or (lambda _e: None)
    setup_telemetry()

    run_id = new_run_id(query)
    run_dir = create_run_dir(settings.output_dir, run_id)
    ledger = UsageLedger()
    budget = Budget(max_cost_usd=settings.effective_max_cost, settings=settings)
    today = datetime.now(UTC).date().isoformat()
    deps = Deps(settings=settings, budget=budget, ledger=ledger, run_id=run_id, today=today)

    record = RunRecord(
        run_id=run_id,
        query=query,
        profile=settings.profile,
        routing=settings.routing,
        models_used={role: resolve_model(role, settings) for role in ROLES},
    )
    timings: dict[str, float] = {}

    try:
        with logfire.span("research run", query=query, profile=settings.profile, run_id=run_id):
            # ---- plan (fatal on failure: nothing to research without one) ----
            t0 = time.perf_counter()
            with logfire.span("plan"):
                plan_run = await planner_agent.run(
                    query,
                    deps=deps,
                    model=resolve_model("planner", settings),
                    usage=ledger.usage_for("planner"),
                    usage_limits=PLANNER_LIMITS,
                )
            plan = plan_run.output
            record.plan = plan
            timings["plan"] = time.perf_counter() - t0
            emit(PlanReady(len(plan.sub_questions), tuple(plan.done_criteria)))

            seen_questions: set[str] = set()
            queue = assign_sub_question_ids(dedup_questions(plan.sub_questions, seen_questions), wave=1)
            record.sub_questions = list(queue)

            # ---- wave 1 (loop arrives in Phase 2) ----
            claims: list[Claim] = []
            wave_n = 1
            t0 = time.perf_counter()
            with logfire.span("wave {wave}", wave=wave_n):
                emit(WaveStarted(wave_n, len(queue)))
                results = await _run_wave(query, queue, deps, emit)
                for sq, res in zip(queue, results, strict=True):
                    if isinstance(res, BaseException):
                        record.failed_sub_questions.append(
                            FailedSubQuestion(
                                sub_question_id=sq.id,
                                error_class=type(res).__name__,
                                message=str(res)[:500],
                            )
                        )
                        emit(ResearcherFailed(sq.id, type(res).__name__))
                    else:
                        claims.extend(
                            enrich_and_dedup_claims(
                                res.claims,
                                sub_question_id=sq.id,
                                wave=wave_n,
                                date_accessed=today,
                                existing=claims,
                            )
                        )
            timings[f"wave_{wave_n}"] = time.perf_counter() - t0
            record.claims = claims
            record.waves_run = wave_n
            _reconcile_searches(ledger)
            emit(CostUpdate(budget.estimate(ledger), budget.max_cost_usd))
    finally:
        # Always flush the audit trail — including on errors and Ctrl-C.
        _reconcile_searches(ledger)
        record.usage = ledger.snapshot(settings)
        record.searches_used = ledger.searches
        record.cost_estimate_usd = round(budget.estimate(ledger), 4)
        record.timings = {k: round(v, 2) for k, v in timings.items()}
        write_run_record(record, run_dir)

    return RunResult(record=record, run_dir=run_dir)


async def _run_wave(
    main_query: str,
    queue: list[SubQuestion],
    deps: Deps,
    emit: Callable[[ProgressEvent], None],
) -> list[Findings | BaseException]:
    settings = deps.settings
    sem = asyncio.Semaphore(settings.effective_concurrency)
    agent = researcher_agent(settings.prof.searches_per_researcher)

    async def one(sq: SubQuestion) -> Findings:
        async with sem:
            emit(ResearcherStarted(sq.id, sq.question))
            result = await agent.run(
                _researcher_prompt(main_query, sq),
                deps=deps,
                model=resolve_model("researcher", settings),
                usage=deps.ledger.usage_for("researcher"),
                usage_limits=RESEARCHER_LIMITS,
            )
            # Conservative upper bound until _reconcile_searches finds real counters.
            deps.ledger.record_searches(settings.prof.searches_per_researcher)
            emit(ResearcherFinished(sq.id, len(result.output.claims)))
            return result.output

    return await asyncio.gather(*[one(sq) for sq in queue], return_exceptions=True)
