"""Offline unit tests for the objective evaluators and judge plumbing (no real models,
mocked network; judge agents exercised via Agent.override + TestModel)."""

import httpx
import pytest
from pydantic_ai.models.test import TestModel
from pydantic_evals.evaluators import EvaluatorContext

from deepresearch.models import Claim, RunRecord, Verdict
from evals.common import EvalOutput
from evals.evaluators import (
    CitationCoverage,
    CitationDensity,
    CitationIntegrity,
    UnsupportedLeakage,
    URLResolution,
    VerifiedClaimRate,
    body_sentences,
    report_body_paragraphs,
)
from evals.judges import CitationSupport, entailment_judge

REPORT = """\
# Title

> **TL;DR** — Short answer.

## Section A

Cited paragraph with a fact [1].

Uncited paragraph without any marker.

## Limitations

- something

## References

1. Src — <https://example.com> (accessed 2026-07-18)
"""


def _record(verdicts: list[Verdict], claims: list[Claim] | None = None) -> RunRecord:
    return RunRecord(
        run_id="r",
        query="q",
        profile="quick",
        routing="gateway",
        claims=claims or [],
        verdicts=verdicts,
    )


def _ctx(output: EvalOutput) -> EvaluatorContext:
    return EvaluatorContext(
        name="case",
        inputs="q",
        metadata=None,
        expected_output=None,
        output=output,
        duration=1.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )


def _claim(cid: str, statement: str, url: str = "https://example.com/a") -> Claim:
    return Claim(
        id=cid,
        sub_question_id="sq-01",
        wave=1,
        date_accessed="2026-07-18",
        statement=statement,
        supporting_quote="q",
        source_url=url,
        source_title="t",
        confidence="high",
    )


def test_report_body_paragraphs_excludes_tail_headings_and_tldr():
    paragraphs = report_body_paragraphs(REPORT)
    assert paragraphs == ["Cited paragraph with a fact [1].", "Uncited paragraph without any marker."]


def test_citation_coverage_half():
    out = EvalOutput(report_markdown=REPORT, record=_record([]))
    assert CitationCoverage().evaluate(_ctx(out)) == {"citation_coverage": 0.5}


def test_citation_density_counts_sentences():
    # Body: "Cited paragraph with a fact [1]." + "Uncited paragraph without any marker."
    out = EvalOutput(report_markdown=REPORT, record=_record([]))
    assert CitationDensity().evaluate(_ctx(out)) == {"citation_density": 0.5}
    two_sentence = REPORT.replace(
        "Cited paragraph with a fact [1].", "Cited fact [1]. Trailing uncited sentence here."
    )
    out2 = EvalOutput(report_markdown=two_sentence, record=_record([]))
    assert CitationDensity().evaluate(_ctx(out2)) == {"citation_density": pytest.approx(1 / 3)}


def test_body_sentences_splits_paragraphs():
    assert body_sentences("# T\n\nOne. Two [1]! Three?\n\n## References\n\n1. x") == [
        "One.",
        "Two [1]!",
        "Three?",
    ]


async def test_citation_support_judges_cited_sentences():
    claims = [
        _claim("c-001", "Paris is the capital of France.", "https://en.wikipedia.org/wiki/Paris"),
        _claim("c-002", "Paris has 2.1M inhabitants.", "https://insee.fr/stats"),
    ]
    report = (
        "# T\n\n> **TL;DR** — x.\n\n## S\n\nParis is the capital [1]. It has 2.1M people [2].\n\n"
        "## References\n\n1. a\n2. b\n"
    )
    out = EvalOutput(report_markdown=report, record=_record([], claims))
    with entailment_judge.override(model=TestModel(custom_output_args={"supported": True})):
        result = await CitationSupport(model="unused-under-override").evaluate(_ctx(out))
    assert result == {"citation_accuracy": 1.0}


async def test_citation_support_not_applicable_without_citations():
    out = EvalOutput(report_markdown="# T\n\nNo citations here.", record=_record([], []))
    assert await CitationSupport(model="x").evaluate(_ctx(out)) == {}


def test_citation_integrity_passes_on_sound_report():
    out = EvalOutput(report_markdown=REPORT, record=_record([]))  # body cites [1]; one reference
    assert CitationIntegrity().evaluate(_ctx(out)) == {"citation_integrity": True}


def test_citation_integrity_flags_dangling_citation():
    broken = REPORT.replace("a fact [1]", "a fact [2]")  # [2] with only one reference -> dangling
    out = EvalOutput(report_markdown=broken, record=_record([]))
    assert CitationIntegrity().evaluate(_ctx(out)) == {"citation_integrity": False}


def test_citation_integrity_not_applicable_without_references():
    out = EvalOutput(report_markdown="# Title\n\nNo references here.", record=_record([]))
    assert CitationIntegrity().evaluate(_ctx(out)) == {}


def test_verified_claim_rate_ignores_unverifiable():
    verdicts = [
        Verdict(claim_id="c-001", verdict="supported"),
        Verdict(claim_id="c-002", verdict="partial"),
        Verdict(claim_id="c-003", verdict="unverifiable"),
    ]
    out = EvalOutput(report_markdown="", record=_record(verdicts))
    assert VerifiedClaimRate().evaluate(_ctx(out)) == {"verified_claim_rate": 0.5}


def test_verified_claim_rate_not_applicable_without_verdicts():
    out = EvalOutput(report_markdown="", record=_record([]))
    assert VerifiedClaimRate().evaluate(_ctx(out)) == {}


def test_unsupported_leakage_detects_leak():
    claims = [_claim("c-001", "Cited paragraph with a fact")]
    verdicts = [Verdict(claim_id="c-001", verdict="unsupported")]
    out = EvalOutput(report_markdown=REPORT, record=_record(verdicts, claims))
    assert UnsupportedLeakage().evaluate(_ctx(out)) == {"no_unsupported_leakage": False}


def test_unsupported_leakage_passes_when_excluded():
    claims = [_claim("c-001", "A statement that was excluded from the report")]
    verdicts = [Verdict(claim_id="c-001", verdict="unsupported")]
    out = EvalOutput(report_markdown=REPORT, record=_record(verdicts, claims))
    assert UnsupportedLeakage().evaluate(_ctx(out)) == {"no_unsupported_leakage": True}


async def test_url_resolution_with_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        status = {"ok.example.com": 200, "forbidden.example.com": 403, "gone.example.com": 404}[
            request.url.host
        ]
        return httpx.Response(status)

    claims = [
        _claim("c-001", "s1", "https://ok.example.com/x"),
        _claim("c-002", "s2", "https://forbidden.example.com/y"),  # bot-blocked counts as alive
        _claim("c-003", "s3", "https://gone.example.com/z"),
    ]
    out = EvalOutput(report_markdown="", record=_record([], claims))
    evaluator = URLResolution()
    evaluator._transport = httpx.MockTransport(handler)
    result = await evaluator.evaluate(_ctx(out))
    assert result == {"url_resolution": pytest.approx(2 / 3)}
