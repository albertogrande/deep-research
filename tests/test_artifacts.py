"""Golden-file test for report assembly: fixed inputs -> exact Markdown out."""

from deepresearch.artifacts import assemble_report, claims_dump_report
from deepresearch.digest import build_citation_map
from deepresearch.models import Claim, Outline, OutlineSection, SubQuestion, Verdict


def _claims() -> list[Claim]:
    return [
        Claim(
            id="c-001",
            sub_question_id="sq-01",
            wave=1,
            date_accessed="2026-07-18",
            statement="Paris is the capital of France.",
            supporting_quote="Paris is the capital and largest city of France.",
            source_url="https://en.wikipedia.org/wiki/Paris",
            source_title="Paris - Wikipedia",
            confidence="high",
        ),
        Claim(
            id="c-002",
            sub_question_id="sq-01",
            wave=1,
            date_accessed="2026-07-18",
            statement="Paris hosts about 2.1 million residents.",
            supporting_quote="population of 2,102,650",
            source_url="https://www.insee.fr/en/statistiques",
            source_title="INSEE population figures",
            confidence="medium",
        ),
    ]


GOLDEN_REPORT = """\
# Paris in Brief

> **TL;DR** — Paris is the capital of France.

## The Answer

Paris is the capital of France [1]. It hosts about 2.1 million residents [2]†.

*Citations marked with † could not be independently re-verified.*

## Limitations

- 1 claim could not be re-verified

## References

1. Paris - Wikipedia — <https://en.wikipedia.org/wiki/Paris> (accessed 2026-07-18)
2. INSEE population figures — <https://www.insee.fr/en/statistiques> (accessed 2026-07-18)
"""


def test_assemble_report_golden():
    claims = _claims()
    citations = build_citation_map(claims)
    outline = Outline(
        title="Paris in Brief",
        tldr="Paris is the capital of France.",
        sections=[OutlineSection(title="The Answer", goal="answer", claim_ids=["c-001", "c-002"])],
    )
    report = assemble_report(
        outline,
        ["Paris is the capital of France [1]. It hosts about 2.1 million residents [2]†."],
        citations,
        ["1 claim could not be re-verified"],
        "2026-07-18",
        unverifiable_count=1,
    )
    assert report == GOLDEN_REPORT


def test_claims_dump_report_structure():
    claims = _claims()
    citations = build_citation_map(claims)
    sub_qs = [SubQuestion(id="sq-01", question="What is the capital of France?", rationale="r", wave=1)]
    verdicts = {
        "c-001": Verdict(claim_id="c-001", verdict="supported"),
        "c-002": Verdict(claim_id="c-002", verdict="unverifiable"),
    }
    dump = claims_dump_report(
        "What is the capital of France?",
        sub_qs,
        claims,
        verdicts,
        citations,
        [],
        "2026-07-18",
        reason="synthesis failed: RuntimeError",
    )
    assert "# Research findings (unsynthesized): What is the capital of France?" in dump
    assert "- Paris is the capital of France. [1] *(supported)*" in dump
    assert "- Paris hosts about 2.1 million residents. [2]† *(unverifiable)*" in dump
    assert "1. Paris - Wikipedia" in dump
