"""Model-judged evaluators: FACT-style citation accuracy and a RACE-style quality rubric.

Judge agents live HERE, in evals/ — the src/deepresearch/agents/ package is for pipeline
agents only. Every judge takes an explicit Anthropic model (the library default is OpenAI).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext, LLMJudge

from deepresearch.digest import build_citation_map, canonical_url
from deepresearch.models import Claim

from .common import EvalOutput
from .evaluators import _CITE_NUM, body_sentences

# FACT-style sampling bound: sentences judged per case (cost control; deterministic spread).
CITATION_SAMPLE_SIZE = 8


class EntailmentVerdict(BaseModel):
    supported: bool
    reasoning: str = ""


entailment_judge: Agent[None, EntailmentVerdict] = Agent(
    output_type=EntailmentVerdict,
    instructions=(
        "You check one citation in a research report. Given a report sentence and the "
        "claim(s) behind its citation marker (each with a verbatim source quote), decide "
        "whether the sentence is SUPPORTED by those quotes: every factual assertion in the "
        "sentence must follow from them, allowing paraphrase and rounding. Attribution "
        "hedges ('according to X') don't weaken support. Anything asserted beyond the "
        "quotes makes it unsupported."
    ),
    retries=2,
)


def _sample_evenly(items: list[str], k: int) -> list[str]:
    """Deterministic spread over the report (no RNG in evals): every len/k-th sentence."""
    if len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def _usable_claims(output: EvalOutput) -> list[Claim]:
    """Reconstruct the claim set the report was assembled from: everything except
    source-contradicted claims — mirrors the orchestrator's exclusion rule, so the rebuilt
    citation numbering matches the report's."""
    unsupported = {v.claim_id for v in output.record.verdicts if v.verdict == "unsupported"}
    return [c for c in output.record.claims if c.id not in unsupported]


@dataclass
class CitationSupport(Evaluator[str, EvalOutput, Any]):
    """FACT-style citation accuracy: sample cited sentences, map each [n] back to its claims
    through the code-owned citation map, and ask a judge whether the sentence is entailed by
    the claims' verbatim quotes. DeepResearch Bench reports even top products at only 78-90%
    on this metric — publishing ours is the point.

    LIVE evaluator: one judge call per sampled sentence (≤ CITATION_SAMPLE_SIZE per case).
    """

    model: str = ""  # explicit Anthropic judge model — never the library default

    async def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        output = ctx.output
        usable = _usable_claims(output)
        if not usable:
            return {}
        citations = build_citation_map(usable)
        claims_by_number: dict[int, list[Claim]] = {}
        for claim in usable:
            claims_by_number.setdefault(citations.number_for(claim), []).append(claim)

        cited_sentences = [s for s in body_sentences(output.report_markdown) if _CITE_NUM.search(s)]
        if not cited_sentences:
            return {}

        supported = 0
        judged = 0
        for sentence in _sample_evenly(cited_sentences, CITATION_SAMPLE_SIZE):
            numbers = [int(n) for n in _CITE_NUM.findall(sentence)]
            backing = [c for n in numbers for c in claims_by_number.get(n, [])]
            if not backing:
                continue  # dangling number — CitationIntegrity flags this deterministically
            evidence = "\n".join(
                f'- [{citations.number_for(c)}] {c.statement}\n  quote: "{c.supporting_quote}" '
                f"(source: {canonical_url(c.source_url)})"
                for c in backing
            )
            prompt = f"SENTENCE:\n{sentence}\n\nCLAIMS BEHIND ITS CITATIONS:\n{evidence}"
            verdict = (await entailment_judge.run(prompt, model=self.model)).output
            judged += 1
            supported += verdict.supported
        if not judged:
            return {}
        return {"citation_accuracy": supported / judged}


def race_judges(model: str) -> list[LLMJudge]:
    """RACE-style report-quality rubric (comprehensiveness / insight / readability) —
    complements the existing completeness/faithfulness/premise judges in run_evals."""
    return [
        LLMJudge(
            rubric=(
                "Comprehensiveness: the report covers the breadth the question implies — "
                "major facets, relevant timeframes, and opposing evidence where it exists — "
                "without padding or repetition."
            ),
            model=model,
            include_input=True,
            score={"evaluation_name": "comprehensiveness"},
            assertion=False,
        ),
        LLMJudge(
            rubric=(
                "Insight: the report synthesizes rather than lists — it connects findings, "
                "notes tensions between sources, and separates established fact from "
                "interpretation."
            ),
            model=model,
            include_input=True,
            score={"evaluation_name": "insight"},
            assertion=False,
        ),
        LLMJudge(
            rubric=(
                "Readability: tight structure, information-dense prose, absolute dates, no "
                "filler or meta-commentary; a busy expert could act on it after one read."
            ),
            model=model,
            include_input=False,
            score={"evaluation_name": "readability"},
            assertion=False,
        ),
    ]
