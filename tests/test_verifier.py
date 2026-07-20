"""Verifier tests: per-source grouping, fetch_ok consistency, verdict merge + backfill."""

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from deepresearch.agents.planner import planner_agent
from deepresearch.agents.researcher import researcher_agent
from deepresearch.agents.verifier import verifier_agent
from deepresearch.config import Settings
from deepresearch.digest import group_claims_by_url
from deepresearch.models import Claim
from deepresearch.orchestrator import run_research
from tests.conftest import FINDINGS_ARGS

QUERY = "What is the capital of France?"


def _claim(cid: str, url: str) -> Claim:
    return Claim(
        id=cid,
        sub_question_id="sq-01",
        wave=1,
        date_accessed="2026-07-18",
        statement=f"statement {cid}",
        supporting_quote="quote",
        source_url=url,
        source_title="title",
        confidence="high",
    )


def test_group_claims_by_url_canonicalizes():
    claims = [
        _claim("c-001", "https://en.wikipedia.org/wiki/Paris"),
        _claim("c-002", "https://www.en.wikipedia.org/wiki/Paris/"),  # same canonical source
        _claim("c-003", "https://example.com/other"),
    ]
    grouped = group_claims_by_url(claims)
    assert len(grouped) == 2
    assert {c.id for c in grouped["https://en.wikipedia.org/wiki/Paris"]} == {"c-001", "c-002"}


@pytest.fixture
def quick_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))


def _sniff_claim_ids(messages) -> list[str]:
    prompt = "".join(str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", []))
    return [f"c-{i:03d}" for i in range(1, 20) if f"c-{i:03d}" in prompt]


async def test_verdicts_merged_and_missing_backfilled(quick_settings, planner_model):
    """Scripted verifier: supports every claim it sees except it 'forgets' c-002 -> backfilled."""

    def scripted_verifier(messages, info: AgentInfo) -> ModelResponse:
        ids = _sniff_claim_ids(messages)
        verdicts = [
            {"claim_id": cid, "verdict": "supported", "reasoning": "matches page"}
            for cid in ids
            if cid != "c-002"
        ]
        args = {"source_url": "https://en.wikipedia.org/wiki/Paris", "fetch_ok": True, "verdicts": verdicts}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(scripted_verifier), native_tools=[]),
    ):
        result = await run_research(QUERY, quick_settings)

    record = result.record
    assert len(record.verdicts) == len(record.claims) == 2
    by_id = {v.claim_id: v for v in record.verdicts}
    assert by_id["c-001"].verdict == "supported"
    assert by_id["c-002"].verdict == "unverifiable"  # skipped by model -> backfilled by code
    assert by_id["c-002"].reasoning == "verifier returned no verdict"
    assert any("could not be re-verified" in lim for lim in record.limitations)


async def test_fetch_ok_false_forces_unverifiable_via_validator(quick_settings, planner_model):
    """First response violates the fetch_ok=false => all-unverifiable rule; ModelRetry fixes it."""
    attempts = {"n": 0}

    def flaky_fetch_verifier(messages, info: AgentInfo) -> ModelResponse:
        ids = _sniff_claim_ids(messages)
        attempts["n"] += 1
        verdict = "supported" if attempts["n"] == 1 else "unverifiable"  # 1st reply is inconsistent
        args = {
            "source_url": "https://en.wikipedia.org/wiki/Paris",
            "fetch_ok": False,
            "verdicts": [{"claim_id": cid, "verdict": verdict, "reasoning": "x"} for cid in ids],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(flaky_fetch_verifier), native_tools=[]),
    ):
        result = await run_research(QUERY, quick_settings)

    assert attempts["n"] >= 2  # validator forced a retry
    assert all(v.verdict == "unverifiable" for v in result.record.verdicts)


async def test_verifier_crash_degrades_to_unverifiable(quick_settings, planner_model):
    def crashing_verifier(messages, info: AgentInfo) -> ModelResponse:
        raise RuntimeError("boom")

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(crashing_verifier), native_tools=[]),
    ):
        result = await run_research(QUERY, quick_settings)

    record = result.record
    assert record.claims  # research survived
    assert all(v.verdict == "unverifiable" for v in record.verdicts)
    assert all("verifier failed" in v.reasoning for v in record.verdicts)


async def test_budget_exhaustion_mid_verification_skips_remaining_sources(
    quick_settings, planner_model, monkeypatch
):
    """Budget runs out after the first source: later sources degrade to 'skipped' verdicts
    while the verdicts already earned are kept."""
    import deepresearch.deps as deps_mod
    import deepresearch.orchestrator as orch

    monkeypatch.setattr(orch, "VERIFY_CONCURRENCY", 1)  # deterministic source order
    calls = {"n": 0}

    def fake_remaining(self, ledger):
        calls["n"] += 1
        # call 1: stage-entry check; call 2: first source's guard; then the money is gone.
        return 1.0 if calls["n"] <= 2 else 0.0

    monkeypatch.setattr(deps_mod.Budget, "remaining_fraction", fake_remaining)

    two_source_findings = {
        "claims": [
            {
                "statement": "Paris is the capital of France.",
                "supporting_quote": "q1",
                "source_url": "https://a.example.com/one",
                "source_title": "A",
                "confidence": "high",
            },
            {
                "statement": "Paris has about 2.1 million inhabitants.",
                "supporting_quote": "q2",
                "source_url": "https://b.example.org/two",
                "source_title": "B",
                "confidence": "medium",
            },
        ],
        "search_queries_used": [],
        "notes": "",
        "summary": "done",
    }

    def scripted_verifier(messages, info: AgentInfo) -> ModelResponse:
        ids = _sniff_claim_ids(messages)
        args = {
            "source_url": "https://a.example.com/one",
            "fetch_ok": True,
            "verdicts": [{"claim_id": cid, "verdict": "supported", "reasoning": "ok"} for cid in ids],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    r_agent = researcher_agent(quick_settings.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=two_source_findings), native_tools=[]),
        verifier_agent.override(model=FunctionModel(scripted_verifier), native_tools=[]),
    ):
        result = await run_research(QUERY, quick_settings)

    by_id = {v.claim_id: v for v in result.record.verdicts}
    assert by_id["c-001"].verdict == "supported"  # earned before the cap hit
    assert by_id["c-002"].verdict == "unverifiable"
    assert by_id["c-002"].reasoning == "skipped: budget cap reached mid-verification"


async def test_no_verify_flag_skips_verification(quick_settings, planner_model):
    no_verify = quick_settings.model_copy(update={"verify": False})

    def exploding_verifier(messages, info: AgentInfo) -> ModelResponse:  # pragma: no cover
        raise AssertionError("verifier must not run with verify=False")

    r_agent = researcher_agent(no_verify.prof.searches_per_researcher)
    with (
        planner_agent.override(model=planner_model),
        r_agent.override(model=TestModel(custom_output_args=FINDINGS_ARGS), native_tools=[]),
        verifier_agent.override(model=FunctionModel(exploding_verifier), native_tools=[]),
    ):
        result = await run_research(QUERY, no_verify)

    assert result.record.verdicts == []
    assert any("--no-verify" in lim for lim in result.record.limitations)
