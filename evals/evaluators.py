"""Objective evaluators over the report text and the RunRecord audit trail.

These never call models. URLResolution uses plain httpx on purpose — evaluators must not
consume model budget nor depend on the in-context-URL fetch semantics of server tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from .common import EvalOutput

_CITATION = re.compile(r"\[\d+\]")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text.casefold()).strip()


def report_body_paragraphs(report_markdown: str) -> list[str]:
    """Body paragraphs between the TL;DR and the Limitations/References tail — the part
    whose factual sentences must carry citations."""
    body = report_markdown
    for tail in ("## Limitations", "## References"):
        idx = body.find(tail)
        if idx != -1:
            body = body[:idx]
    paragraphs = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(("#", ">", "*Citations marked")):
            continue
        paragraphs.append(block)
    return paragraphs


@dataclass
class CitationCoverage(Evaluator[str, EvalOutput, Any]):
    """Fraction of body paragraphs containing at least one [n] citation."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        paragraphs = report_body_paragraphs(ctx.output.report_markdown)
        if not paragraphs:
            return {}
        covered = sum(1 for p in paragraphs if _CITATION.search(p))
        return {"citation_coverage": covered / len(paragraphs)}


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def body_sentences(report_markdown: str) -> list[str]:
    """Sentences of the body paragraphs — the FACT-style unit of citation accounting."""
    sentences: list[str] = []
    for paragraph in report_body_paragraphs(report_markdown):
        sentences += [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
    return sentences


@dataclass
class CitationDensity(Evaluator[str, EvalOutput, Any]):
    """Fraction of body SENTENCES carrying a [n] citation — finer-grained than the
    paragraph-level CitationCoverage (both are kept; density is the stricter signal)."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        sentences = body_sentences(ctx.output.report_markdown)
        if not sentences:
            return {}
        cited = sum(1 for s in sentences if _CITATION.search(s))
        return {"citation_density": cited / len(sentences)}


_REF_LINE = re.compile(r"^(\d+)\.\s", re.MULTILINE)
_CITE_NUM = re.compile(r"\[(\d+)\]")


@dataclass
class CitationIntegrity(Evaluator[str, EvalOutput, Any]):
    """Deterministic citation soundness (borrowed from the semantica-ai evals plan): every
    ``[n]`` in the body resolves to a listed reference, and references are numbered
    contiguously from 1. Passing = True. Not applicable to reference-less fallback reports."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, bool]:
        report = ctx.output.report_markdown
        idx = report.find("## References")
        if idx == -1:
            return {}
        ref_nums = [int(m.group(1)) for m in _REF_LINE.finditer(report[idx:])]
        if not ref_nums:
            return {}
        body = "\n".join(report_body_paragraphs(report))
        body_nums = {int(n) for n in _CITE_NUM.findall(body)}
        contiguous = ref_nums == list(range(1, len(ref_nums) + 1))
        no_dangling = body_nums <= set(ref_nums)
        return {"citation_integrity": contiguous and no_dangling}


@dataclass
class VerifiedClaimRate(Evaluator[str, EvalOutput, Any]):
    """supported / (total - unverifiable). Not applicable when verification didn't run."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        verdicts = ctx.output.record.verdicts
        judged = [v for v in verdicts if v.verdict != "unverifiable"]
        if not judged:
            return {}
        supported = sum(1 for v in judged if v.verdict == "supported")
        return {"verified_claim_rate": supported / len(judged)}


@dataclass
class UnverifiableRate(Evaluator[str, EvalOutput, Any]):
    """Fraction of verdicts that are unverifiable — the paywall/403 reality check."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        verdicts = ctx.output.record.verdicts
        if not verdicts:
            return {}
        unverifiable = sum(1 for v in verdicts if v.verdict == "unverifiable")
        return {"unverifiable_rate": unverifiable / len(verdicts)}


@dataclass
class UnsupportedLeakage(Evaluator[str, EvalOutput, Any]):
    """Assertion: no source-contradicted (unsupported) claim's statement leaks into the
    report body. Passing = True."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, bool]:
        record = ctx.output.record
        unsupported_ids = {v.claim_id for v in record.verdicts if v.verdict == "unsupported"}
        if not unsupported_ids:
            return {}
        body = _normalize("\n".join(report_body_paragraphs(ctx.output.report_markdown)))
        leaked = [
            c.id
            for c in record.claims
            if c.id in unsupported_ids and _normalize(c.statement).rstrip(".") in body
        ]
        return {"no_unsupported_leakage": not leaked}


@dataclass
class URLResolution(Evaluator[str, EvalOutput, Any]):
    """Fraction of cited source URLs that still resolve (status <400, or 403 =
    reachable-but-bot-blocked). Plain httpx, never agent tools."""

    timeout_s: float = 10.0

    async def evaluate(self, ctx: EvaluatorContext[str, EvalOutput, Any]) -> dict[str, float]:
        record = ctx.output.record
        unsupported = {v.claim_id for v in record.verdicts if v.verdict == "unsupported"}
        urls = sorted({c.source_url for c in record.claims if c.id not in unsupported})
        if not urls:
            return {}
        ok = 0
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout_s,
            headers={"User-Agent": "Mozilla/5.0 (deepresearch-evals)"},
            transport=getattr(self, "_transport", None),
        ) as client:
            for url in urls:
                if await self._resolves(client, url):
                    ok += 1
        return {"url_resolution": ok / len(urls)}

    async def _resolves(self, client: httpx.AsyncClient, url: str) -> bool:
        try:
            response = await client.head(url)
            if response.status_code >= 400 and response.status_code != 403:
                response = await client.get(url)
            return response.status_code < 400 or response.status_code == 403
        except httpx.HTTPError:
            return False
