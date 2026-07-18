"""Pipeline orchestration: sequencing, concurrency, budget, and error policy live HERE.

Phase 2 scope: plan → wave loop (research → gap analysis → follow-ups, until saturated,
out of waves, or out of budget). Verification and synthesis arrive in Phases 3-4.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import logfire
from pydantic_ai import UsageLimits
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from .agents.gap_analyst import MAX_FOLLOW_UPS, gap_analyst_agent
from .agents.planner import planner_agent
from .agents.researcher import researcher_agent
from .artifacts import create_run_dir, new_run_id, write_run_record
from .config import Role, Settings, resolve_model
from .deps import Budget, BudgetExceeded, Deps, UsageLedger
from .digest import (
    assign_sub_question_ids,
    dedup_questions,
    enrich_and_dedup_claims,
    gap_digest,
)
from .models import Claim, FailedSubQuestion, Findings, RunRecord, SubQuestion
from .progress import (
    CostUpdate,
    GapResult,
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
GAP_LIMITS = UsageLimits(request_limit=5)

TRANSIENT_RETRY_DELAY_S = 2.0  # module-level so tests can monkeypatch it away


class ErrorClass(enum.Enum):
    TRANSIENT = "transient"  # 429/5xx/network: worth one retry
    DEGRADABLE = "degradable"  # this unit failed; the run continues without it
    BUDGET_FATAL = "budget_fatal"  # provider/gateway refuses on payment/spend grounds
    FATAL = "fatal"  # nothing sensible can continue (e.g. bad credentials)


def classify_error(e: BaseException) -> ErrorClass:
    if isinstance(e, ModelHTTPError):
        if e.status_code in (402, 403):
            return ErrorClass.BUDGET_FATAL  # gateway spend caps surface as payment/forbidden
        if e.status_code == 401:
            return ErrorClass.FATAL
        if e.status_code == 429 or e.status_code >= 500:
            return ErrorClass.TRANSIENT
        return ErrorClass.DEGRADABLE
    if isinstance(e, ModelAPIError):
        return ErrorClass.TRANSIENT  # connection-level trouble
    if isinstance(e, UsageLimitExceeded | UnexpectedModelBehavior):
        return ErrorClass.DEGRADABLE
    if isinstance(e, TimeoutError | asyncio.TimeoutError | ConnectionError):
        return ErrorClass.TRANSIENT
    return ErrorClass.DEGRADABLE


class BudgetFatalError(Exception):
    """A provider/gateway refusal that makes further model calls pointless."""


@dataclass
class RunResult:
    record: RunRecord
    run_dir: Path
    report_path: Path | None = None


@dataclass
class _RunState:
    claims: list[Claim] = field(default_factory=list)
    sub_questions: list[SubQuestion] = field(default_factory=list)
    seen_questions: set[str] = field(default_factory=set)
    notes_by_sq: dict[str, str] = field(default_factory=dict)
    failed_sq_ids: set[str] = field(default_factory=set)
    limitations: list[str] = field(default_factory=list)


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
    state = _RunState()

    try:
        with logfire.span("research run", query=query, profile=settings.profile, run_id=run_id):
            plan = await _plan_stage(query, deps, record, timings, emit)
            state.sub_questions = assign_sub_question_ids(
                dedup_questions(plan.sub_questions, state.seen_questions), wave=1
            )
            await _wave_loop(query, plan.done_criteria, deps, record, state, timings, emit)
    finally:
        # Always flush the audit trail — including on errors and Ctrl-C.
        _reconcile_searches(ledger)
        record.sub_questions = state.sub_questions
        record.claims = state.claims
        record.limitations = state.limitations
        record.usage = ledger.snapshot(settings)
        record.searches_used = ledger.searches
        record.cost_estimate_usd = round(budget.estimate(ledger), 4)
        record.timings = {k: round(v, 2) for k, v in timings.items()}
        write_run_record(record, run_dir)

    return RunResult(record=record, run_dir=run_dir)


async def _plan_stage(query, deps: Deps, record: RunRecord, timings, emit):
    """Planning is fatal on failure: with no plan there is nothing to research."""
    settings = deps.settings
    t0 = time.perf_counter()
    with logfire.span("plan"):
        plan_run = await planner_agent.run(
            query,
            deps=deps,
            model=resolve_model("planner", settings),
            usage=deps.ledger.usage_for("planner"),
            usage_limits=PLANNER_LIMITS,
        )
    plan = plan_run.output
    record.plan = plan
    timings["plan"] = time.perf_counter() - t0
    emit(PlanReady(len(plan.sub_questions), tuple(plan.done_criteria)))
    return plan


async def _wave_loop(query, done_criteria, deps: Deps, record, state: _RunState, timings, emit) -> None:
    settings = deps.settings
    queue = list(state.sub_questions)

    for wave_n in range(1, settings.prof.max_waves + 1):
        t0 = time.perf_counter()
        with logfire.span("wave {wave}", wave=wave_n, n_questions=len(queue)):
            emit(WaveStarted(wave_n, len(queue)))
            results = await _run_wave(query, queue, deps, emit)
            for sq, res in zip(queue, results, strict=True):
                if isinstance(res, BaseException):
                    _record_failure(record, state, sq, res, emit)
                    if classify_error(res) is ErrorClass.BUDGET_FATAL:
                        state.limitations.append(
                            f"aborted in wave {wave_n}: provider/gateway refused on spend grounds"
                        )
                        record.waves_run = wave_n
                        raise BudgetFatalError(str(res)) from res
                else:
                    state.notes_by_sq[sq.id] = res.notes
                    state.claims.extend(
                        enrich_and_dedup_claims(
                            res.claims,
                            sub_question_id=sq.id,
                            wave=wave_n,
                            date_accessed=deps.today,
                            existing=state.claims,
                        )
                    )
        timings[f"wave_{wave_n}"] = time.perf_counter() - t0
        record.waves_run = wave_n
        emit(CostUpdate(deps.budget.estimate(deps.ledger), deps.budget.max_cost_usd))

        try:
            deps.budget.checkpoint(deps.ledger, f"after wave {wave_n}")
        except BudgetExceeded as e:
            state.limitations.append(f"budget cap reached {e.stage}; no further research waves")
            return

        if wave_n == settings.prof.max_waves:
            if settings.prof.max_waves > 1:
                state.limitations.append(f"stopped at max_waves={settings.prof.max_waves} without saturation")
            return

        gap = await _gap_stage(query, done_criteria, deps, record, state, timings, emit)
        if gap is None or gap.saturated or not gap.follow_up_questions:
            record.saturated = bool(gap and gap.saturated)
            return

        fresh = dedup_questions(gap.follow_up_questions[:MAX_FOLLOW_UPS], state.seen_questions)
        if not fresh:
            state.limitations.append("gap analyst proposed only already-asked questions; stopping")
            return
        queue = assign_sub_question_ids(fresh, wave=wave_n + 1, start_index=len(state.sub_questions) + 1)
        state.sub_questions.extend(queue)


async def _gap_stage(query, done_criteria, deps: Deps, record, state: _RunState, timings, emit):
    """Gap analysis is degradable: on failure we just stop iterating and synthesize."""
    settings = deps.settings
    t0 = time.perf_counter()
    digest = gap_digest(
        query, list(done_criteria), state.sub_questions, state.claims, state.notes_by_sq, state.failed_sq_ids
    )
    try:
        with logfire.span("gap analysis"):
            gap_run = await gap_analyst_agent.run(
                digest,
                deps=deps,
                model=resolve_model("gap_analyst", settings),
                usage=deps.ledger.usage_for("gap_analyst"),
                usage_limits=GAP_LIMITS,
            )
    except Exception as e:
        if classify_error(e) is ErrorClass.BUDGET_FATAL:
            raise BudgetFatalError(str(e)) from e
        state.limitations.append(f"gap analysis failed ({type(e).__name__}); stopped iterating early")
        return None
    finally:
        timings[f"gap_after_wave_{record.waves_run}"] = time.perf_counter() - t0

    gap = gap_run.output
    record.final_coverage = gap.coverage
    emit(GapResult(gap.saturated, len(gap.follow_up_questions)))
    return gap


def _record_failure(record: RunRecord, state: _RunState, sq: SubQuestion, e: BaseException, emit) -> None:
    record.failed_sub_questions.append(
        FailedSubQuestion(sub_question_id=sq.id, error_class=type(e).__name__, message=str(e)[:500])
    )
    state.failed_sq_ids.add(sq.id)
    emit(ResearcherFailed(sq.id, type(e).__name__))


async def _run_wave(
    main_query: str,
    queue: list[SubQuestion],
    deps: Deps,
    emit: Callable[[ProgressEvent], None],
) -> list[Findings | BaseException]:
    settings = deps.settings
    sem = asyncio.Semaphore(settings.effective_concurrency)
    agent = researcher_agent(settings.prof.searches_per_researcher)

    async def attempt(sq: SubQuestion) -> Findings:
        result = await agent.run(
            _researcher_prompt(main_query, sq),
            deps=deps,
            model=resolve_model("researcher", settings),
            usage=deps.ledger.usage_for("researcher"),
            usage_limits=RESEARCHER_LIMITS,
        )
        # Conservative upper bound until _reconcile_searches finds real counters.
        deps.ledger.record_searches(settings.prof.searches_per_researcher)
        return result.output

    async def one(sq: SubQuestion) -> Findings:
        async with sem:
            emit(ResearcherStarted(sq.id, sq.question))
            try:
                findings = await attempt(sq)
            except Exception as e:
                if classify_error(e) is not ErrorClass.TRANSIENT:
                    raise
                await asyncio.sleep(TRANSIENT_RETRY_DELAY_S)
                findings = await attempt(sq)  # one retry for transient trouble
            emit(ResearcherFinished(sq.id, len(findings.claims)))
            return findings

    return await asyncio.gather(*[one(sq) for sq in queue], return_exceptions=True)
