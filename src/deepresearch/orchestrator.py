"""Pipeline orchestration: sequencing, concurrency, budget, and error policy live HERE.

Full pipeline: plan → wave loop (research → gap analysis → follow-ups until saturated/out of
waves/out of budget) → per-source verification → two-stage synthesis → report.md + run.json.
Every stage degrades rather than dying; synthesis is always attempted when claims exist.
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
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.usage import RunUsage

from .agents.critic import critic_agent
from .agents.gap_analyst import MAX_FOLLOW_UPS, gap_analyst_agent
from .agents.planner import planner_agent
from .agents.researcher import researcher_agent
from .agents.synthesizer import outline_agent, section_agent
from .agents.verifier import verifier_agent, verify_prompt
from .artifacts import (
    assemble_report,
    claims_dump_report,
    create_run_dir,
    new_run_id,
    write_report,
    write_run_record,
)
from .config import Role, Settings, resolve_model
from .deps import Budget, BudgetExceeded, Deps, UsageLedger
from .digest import (
    assign_sub_question_ids,
    build_citation_map,
    critic_prompt,
    dedup_questions,
    enrich_and_dedup_claims,
    gap_digest,
    group_claims_by_url,
    known_so_far_brief,
    outline_digest,
    section_prompt,
)
from .models import (
    Claim,
    FailedSubQuestion,
    Findings,
    RunRecord,
    SourceVerification,
    SubQuestion,
    Verdict,
)
from .progress import (
    CostUpdate,
    CritiqueResult,
    GapResult,
    PlanReady,
    ProgressEvent,
    ResearcherFailed,
    ResearcherFinished,
    ResearcherStarted,
    SynthesisStage,
    VerificationProgress,
    WaveStarted,
)
from .telemetry import current_trace_id, setup_telemetry

ROLES: tuple[Role, ...] = (
    "planner",
    "researcher",
    "gap_analyst",
    "verifier",
    "synthesizer",
    "critic",
)

PLANNER_LIMITS = UsageLimits(request_limit=5)
RESEARCHER_LIMITS = UsageLimits(request_limit=12, total_tokens_limit=120_000)
GAP_LIMITS = UsageLimits(request_limit=5)
VERIFIER_LIMITS = UsageLimits(request_limit=6, total_tokens_limit=60_000)
OUTLINE_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=60_000)
SECTION_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=40_000)
CRITIC_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=60_000)

VERIFY_CONCURRENCY = 4
# Skip verification when less than this fraction of budget remains — synthesis is the payoff
# and must always be affordable.
VERIFY_MIN_BUDGET_FRACTION = 0.25

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
    summaries_by_sq: dict[str, str] = field(default_factory=dict)
    failed_sq_ids: set[str] = field(default_factory=set)
    limitations: list[str] = field(default_factory=list)


async def _run_agent(
    agent: Agent, prompt: str, *, role: Role, deps: Deps, usage_limits: UsageLimits, **kwargs
):
    """Run an agent with a FRESH per-run RunUsage (so usage_limits apply to this run alone),
    merging the result into the role's cumulative ledger afterwards — even on failure."""
    run_usage = RunUsage()
    try:
        return await agent.run(
            prompt,
            deps=deps,
            model=resolve_model(role, deps.settings),
            usage=run_usage,
            usage_limits=usage_limits,
            **kwargs,
        )
    finally:
        deps.ledger.record(role, run_usage)


def _researcher_prompt(main_query: str, sq: SubQuestion, known_so_far: str = "") -> str:
    prompt = (
        f"Main research question (context only — do not research it directly):\n{main_query}\n\n"
        f"YOUR sub-question ({sq.id}):\n{sq.question}\n\n"
        f"Why it matters: {sq.rationale}"
    )
    if known_so_far:
        prompt += f"\n\n{known_so_far}"
    return prompt


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

    report_path: Path | None = None
    try:
        with logfire.span("research run", query=query, profile=settings.profile, run_id=run_id):
            record.logfire_trace_id = current_trace_id()
            try:
                plan = await _plan_stage(query, deps, record, timings, emit)
                state.sub_questions = assign_sub_question_ids(
                    dedup_questions(plan.sub_questions, state.seen_questions), wave=1
                )
                await _wave_loop(query, plan.done_criteria, deps, record, state, timings, emit)
                await _verification_stage(deps, record, state, timings, emit)
            except BudgetFatalError:
                # Research aborted mid-flight; leave the best artifact we can, then surface it.
                record.synthesis_ok = False
                report_path = _write_fallback(
                    query, deps, record, state, run_dir, reason="aborted: provider/gateway spend refusal"
                )
                raise
            report_path = await _synthesis_stage(query, deps, record, state, run_dir, timings, emit)
            emit(CostUpdate(deps.budget.estimate(deps.ledger), deps.budget.max_cost_usd))
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

    return RunResult(record=record, run_dir=run_dir, report_path=report_path)


async def _plan_stage(query, deps: Deps, record: RunRecord, timings, emit):
    """Planning is fatal on failure: with no plan there is nothing to research."""
    t0 = time.perf_counter()
    with logfire.span("plan"):
        plan_run = await _run_agent(
            planner_agent, query, role="planner", deps=deps, usage_limits=PLANNER_LIMITS
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
        # Wave-1 researchers run isolated (full parallelism, no shared context to bias them);
        # later waves get a compressed what-we-know brief so follow-ups target the gap.
        known = ""
        if wave_n > 1:
            prior_sqs = [sq for sq in state.sub_questions if sq.wave < wave_n]
            known = known_so_far_brief(prior_sqs, state.summaries_by_sq, state.claims)
        with logfire.span("wave {wave}", wave=wave_n, n_questions=len(queue)):
            emit(WaveStarted(wave_n, len(queue)))
            results = await _run_wave(query, queue, deps, emit, known_so_far=known)
            # Ingest every successful result first so a spend refusal on one researcher never
            # discards the claims the others already gathered.
            budget_fatal: BaseException | None = None
            for sq, res in zip(queue, results, strict=True):
                if isinstance(res, BaseException):
                    _record_failure(record, state, sq, res, emit)
                    if classify_error(res) is ErrorClass.BUDGET_FATAL:
                        budget_fatal = res
                else:
                    state.notes_by_sq[sq.id] = res.notes
                    state.summaries_by_sq[sq.id] = res.summary
                    state.claims.extend(
                        enrich_and_dedup_claims(
                            res.claims,
                            sub_question_id=sq.id,
                            wave=wave_n,
                            date_accessed=deps.today,
                            existing=state.claims,
                        )
                    )
            if budget_fatal is not None:
                state.limitations.append(
                    f"aborted in wave {wave_n}: provider/gateway refused on spend grounds"
                )
                record.waves_run = wave_n
                raise BudgetFatalError(str(budget_fatal)) from budget_fatal
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
    t0 = time.perf_counter()
    digest = gap_digest(
        query,
        list(done_criteria),
        state.sub_questions,
        state.claims,
        state.summaries_by_sq,
        state.notes_by_sq,
        state.failed_sq_ids,
        current_wave=record.waves_run,
    )
    try:
        with logfire.span("gap analysis"):
            gap_run = await _run_agent(
                gap_analyst_agent, digest, role="gap_analyst", deps=deps, usage_limits=GAP_LIMITS
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


async def _verification_stage(deps: Deps, record: RunRecord, state: _RunState, timings, emit) -> None:
    """Per-source claim verification. Degradable per source; skippable by flag or budget."""
    settings = deps.settings
    if not state.claims:
        return
    if not settings.verify:
        state.limitations.append("claim verification skipped (--no-verify)")
        return
    if deps.budget.remaining_fraction(deps.ledger) < VERIFY_MIN_BUDGET_FRACTION:
        state.limitations.append("claim verification skipped: less than 25% of budget remaining")
        return

    by_source = group_claims_by_url(state.claims)
    total = len(by_source)
    t0 = time.perf_counter()
    verdict_by_claim: dict[str, Verdict] = {}
    counters = {"done": 0, "supported": 0, "judged": 0}
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def verify_source(url: str, claims: list[Claim]) -> SourceVerification:
        async with sem:
            try:
                result = await _run_agent(
                    verifier_agent,
                    verify_prompt(url, claims),
                    role="verifier",
                    deps=deps,
                    usage_limits=VERIFIER_LIMITS,
                )
                sv = result.output
            except Exception as e:
                if classify_error(e) is ErrorClass.BUDGET_FATAL:
                    raise BudgetFatalError(str(e)) from e
                # Degrade: an unverifiable source, never a dead run.
                sv = SourceVerification(
                    source_url=url,
                    fetch_ok=False,
                    verdicts=[
                        Verdict(
                            claim_id=c.id,
                            verdict="unverifiable",
                            reasoning=f"verifier failed: {type(e).__name__}",
                        )
                        for c in claims
                    ],
                )
            counters["done"] += 1
            for v in sv.verdicts:
                if v.verdict != "unverifiable":
                    counters["judged"] += 1
                    if v.verdict == "supported":
                        counters["supported"] += 1
            emit(
                VerificationProgress(
                    counters["done"], total, counters["supported"] / max(1, counters["judged"])
                )
            )
            return sv

    with logfire.span("verification", sources=total, claims=len(state.claims)):
        results = await asyncio.gather(*[verify_source(url, claims) for url, claims in by_source.items()])

    valid_ids = {c.id for c in state.claims}
    cited_ids_by_source = {url: {c.id for c in claims} for url, claims in by_source.items()}
    for sv, (url, _) in zip(results, by_source.items(), strict=True):
        for v in sv.verdicts:
            # Ignore hallucinated claim ids; first verdict per claim wins.
            if v.claim_id in valid_ids and v.claim_id in cited_ids_by_source[url]:
                verdict_by_claim.setdefault(v.claim_id, v)
    # Backfill claims the verifier skipped — never silently treated as supported.
    for c in state.claims:
        if c.id not in verdict_by_claim:
            verdict_by_claim[c.id] = Verdict(
                claim_id=c.id, verdict="unverifiable", reasoning="verifier returned no verdict"
            )

    record.verdicts = [verdict_by_claim[c.id] for c in state.claims]
    timings["verification"] = time.perf_counter() - t0

    unverifiable = sum(1 for v in record.verdicts if v.verdict == "unverifiable")
    if unverifiable:
        state.limitations.append(
            f"{unverifiable} of {len(record.verdicts)} claims could not be re-verified "
            "(fetch failures, paywalls, or missing verdicts)"
        )


def _usable_claims(state: _RunState, record: RunRecord) -> tuple[list[Claim], dict[str, Verdict]]:
    """Claims eligible for the report: everything except source-contradicted (unsupported)."""
    verdict_by_claim = {v.claim_id: v for v in record.verdicts}
    excluded = {cid for cid, v in verdict_by_claim.items() if v.verdict == "unsupported"}
    usable = [c for c in state.claims if c.id not in excluded]
    note = f"{len(excluded)} claim(s) contradicted by their cited sources were excluded from the report"
    if excluded and note not in state.limitations:  # idempotent: may be called twice on failure paths
        state.limitations.append(note)
    return usable, verdict_by_claim


def _write_fallback(
    query: str, deps: Deps, record: RunRecord, state: _RunState, run_dir: Path, reason: str
) -> Path | None:
    if not state.claims:
        return None
    usable, verdict_by_claim = _usable_claims(state, record)
    if not usable:
        return None
    citations = build_citation_map(usable)
    report = claims_dump_report(
        query, state.sub_questions, usable, verdict_by_claim, citations, state.limitations, deps.today, reason
    )
    path = write_report(report, run_dir)
    record.report_path = str(path)
    return path


async def _synthesis_stage(
    query: str, deps: Deps, record: RunRecord, state: _RunState, run_dir: Path, timings, emit
) -> Path | None:
    """Always attempted (it is the payoff); degrades to a claims-dump artifact on failure."""
    usable, verdict_by_claim = _usable_claims(state, record)
    if not usable:
        state.limitations.append("no usable claims gathered; no report generated")
        return None

    citations = build_citation_map(usable)
    unverifiable_count = sum(
        1 for c in usable if verdict_by_claim.get(c.id) and verdict_by_claim[c.id].verdict == "unverifiable"
    )
    done_criteria = list(record.plan.done_criteria) if record.plan else []
    t0 = time.perf_counter()
    try:
        with logfire.span("synthesis", sections="tbd", claims=len(usable)):
            deps.outline_valid_claim_ids = frozenset(c.id for c in usable)
            deps.outline_required_claim_ids = frozenset(
                c.id
                for c in usable
                if verdict_by_claim.get(c.id) and verdict_by_claim[c.id].verdict == "supported"
            )
            emit(SynthesisStage("outline"))
            outline_run = await _run_agent(
                outline_agent,
                outline_digest(query, done_criteria, state.sub_questions, usable, verdict_by_claim),
                role="synthesizer",
                deps=deps,
                usage_limits=OUTLINE_LIMITS,
            )
            outline = outline_run.output
            claims_by_id = {c.id: c for c in usable}

            async def write_sections(guidance: str) -> str:
                """Write every section (optionally applying critic guidance) and assemble.
                The outline and citation map stay FIXED across revises, so citation numbering
                never drifts — only the prose changes."""
                stage = "revise" if guidance else "section"
                bodies: list[str] = []
                for i, section in enumerate(outline.sections, start=1):
                    emit(SynthesisStage(stage, i, len(outline.sections)))
                    section_claims = [claims_by_id[cid] for cid in section.claim_ids if cid in claims_by_id]
                    section_run = await _run_agent(
                        section_agent,
                        section_prompt(section, section_claims, citations, verdict_by_claim, guidance),
                        role="synthesizer",
                        deps=deps,
                        usage_limits=SECTION_LIMITS,
                    )
                    bodies.append(section_run.output.markdown)
                return assemble_report(
                    outline, bodies, citations, state.limitations, deps.today, unverifiable_count
                )

            report = await write_sections("")

            # Critic gate + bounded revise loop (borrowed from the deep-research TS pipeline):
            # grade the draft against the plan's acceptance criteria; on "revise", rewrite the
            # sections with the critic's guidance, up to max_revise_iters (0 disables the loop).
            max_revise_iters = deps.settings.max_revise_iters
            for iteration in range(max_revise_iters + 1):
                try:
                    critique = (
                        await _run_agent(
                            critic_agent,
                            critic_prompt(query, done_criteria, report, usable, verdict_by_claim, iteration),
                            role="critic",
                            deps=deps,
                            usage_limits=CRITIC_LIMITS,
                        )
                    ).output
                except Exception as e:  # noqa: BLE001 — a flaky critic must not sink a good draft
                    if classify_error(e) is ErrorClass.BUDGET_FATAL:
                        raise BudgetFatalError(str(e)) from e
                    state.limitations.append(
                        f"critic failed ({type(e).__name__}); shipped un-critiqued draft"
                    )
                    break
                record.critique_verdict = critique.verdict
                record.critique_issues = list(critique.issues)
                emit(CritiqueResult(critique.verdict, iteration, len(critique.issues)))
                if critique.verdict == "ship" or iteration == max_revise_iters:
                    break
                try:
                    deps.budget.checkpoint(deps.ledger, "before revise")
                except BudgetExceeded:
                    state.limitations.append("critic revisions skipped: budget cap reached")
                    break
                record.critique_iterations = iteration + 1
                report = await write_sections(critique.guidance)
    except Exception as e:
        if classify_error(e) is ErrorClass.BUDGET_FATAL:
            record.synthesis_ok = False
            raise BudgetFatalError(str(e)) from e
        record.synthesis_ok = False
        state.limitations.append(f"report synthesis failed ({type(e).__name__}); wrote claims dump instead")
        return _write_fallback(
            query, deps, record, state, run_dir, reason=f"synthesis failed: {type(e).__name__}"
        )
    finally:
        timings["synthesis"] = time.perf_counter() - t0

    path = write_report(report, run_dir)
    record.report_path = str(path)
    return path


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
    known_so_far: str = "",
) -> list[Findings | BaseException]:
    settings = deps.settings
    sem = asyncio.Semaphore(settings.effective_concurrency)
    agent = researcher_agent(settings.prof.searches_per_researcher)

    async def attempt(sq: SubQuestion) -> Findings:
        result = await _run_agent(
            agent,
            _researcher_prompt(main_query, sq, known_so_far),
            role="researcher",
            deps=deps,
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
