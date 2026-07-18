"""Pure functions between pipeline stages: identity assignment, dedup, digests.

No model calls, no I/O — everything here is trivially unit-testable.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Claim, PlannedSubQuestion, RawClaim, SubQuestion

_INTERROGATIVE_PREFIX = re.compile(
    r"^(what|which|who|whom|whose|when|where|why|how)\s+"
    r"(is|are|was|were|does|do|did|has|have|can|could|should|would|will)\s+"
    r"(?:(?:the|a|an)\s+)?",
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_question(q: str) -> str:
    """Casefold, strip punctuation and leading interrogative boilerplate — dedup key."""
    s = q.strip().casefold()
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return _INTERROGATIVE_PREFIX.sub("", s)


def normalize_statement(s: str) -> str:
    t = s.strip().casefold()
    t = _NON_ALNUM.sub(" ", t)
    return _WS.sub(" ", t).strip()


def canonical_url(url: str) -> str:
    """Strip scheme, www., query params, fragments, and trailing slash — claim dedup key."""
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").casefold().removeprefix("www.")
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"


def assign_sub_question_ids(
    planned: list[PlannedSubQuestion], *, wave: int, start_index: int = 1
) -> list[SubQuestion]:
    """Turn planner/gap-analyst output into identified SubQuestions; numbering continues across waves."""
    return [
        SubQuestion(id=f"sq-{start_index + i:02d}", question=p.question, rationale=p.rationale, wave=wave)
        for i, p in enumerate(planned)
    ]


def dedup_questions(planned: list[PlannedSubQuestion], seen_normalized: set[str]) -> list[PlannedSubQuestion]:
    """Drop questions already asked (mutates seen_normalized with the survivors)."""
    fresh: list[PlannedSubQuestion] = []
    for p in planned:
        key = normalize_question(p.question)
        if key and key not in seen_normalized:
            seen_normalized.add(key)
            fresh.append(p)
    return fresh


def gap_digest(
    query: str,
    done_criteria: list[str],
    sub_questions: list[SubQuestion],
    claims: list[Claim],
    notes_by_sq: dict[str, str],
    failed_sq_ids: set[str],
) -> str:
    """Render what the gap analyst sees: statements, counts, domains, notes — never quotes,
    never full URLs. Keeps a standard run's digest well under ~3k tokens."""
    by_sq: dict[str, list[Claim]] = {}
    for c in claims:
        by_sq.setdefault(c.sub_question_id, []).append(c)

    lines: list[str] = [f"MAIN QUESTION: {query}", "", "DONE CRITERIA:"]
    lines += [f"- {d}" for d in done_criteria]
    lines += ["", "SUB-QUESTIONS AND CLAIMS SO FAR:"]
    for sq in sub_questions:
        sq_claims = by_sq.get(sq.id, [])
        domains = sorted({canonical_url(c.source_url).split("/")[0] for c in sq_claims})
        status = " [RESEARCHER FAILED]" if sq.id in failed_sq_ids else ""
        lines.append(f"\n{sq.id} (wave {sq.wave}){status}: {sq.question}")
        lines.append(f"  claims: {len(sq_claims)} | distinct sources: {len(domains)}")
        for c in sq_claims:
            corroborated = f" (x{c.corroborations + 1})" if c.corroborations else ""
            lines.append(f"  - [{c.confidence}] {c.statement}{corroborated}")
        if notes := notes_by_sq.get(sq.id, "").strip():
            lines.append(f"  researcher notes: {notes}")
    lines += ["", "ALREADY-ASKED QUESTIONS (follow-ups must NOT restate these):"]
    lines += [f"- {sq.question}" for sq in sub_questions]
    return "\n".join(lines)


def enrich_and_dedup_claims(
    raw: list[RawClaim],
    *,
    sub_question_id: str,
    wave: int,
    date_accessed: str,
    existing: list[Claim],
) -> list[Claim]:
    """Assign claim ids and fold duplicates (same canonical URL + normalized statement)
    into the existing claim's ``corroborations`` counter. Returns only the new claims;
    numbering continues from ``existing``."""
    index = {(canonical_url(c.source_url), normalize_statement(c.statement)): c for c in existing}
    new_claims: list[Claim] = []
    next_n = len(existing) + 1
    for rc in raw:
        key = (canonical_url(rc.source_url), normalize_statement(rc.statement))
        if key in index:
            index[key].corroborations += 1
            continue
        claim = Claim(
            id=f"c-{next_n:03d}",
            sub_question_id=sub_question_id,
            wave=wave,
            date_accessed=date_accessed,
            **rc.model_dump(),
        )
        index[key] = claim
        new_claims.append(claim)
        next_n += 1
    return new_claims
