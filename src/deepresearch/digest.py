"""Pure functions between pipeline stages: identity assignment, dedup, digests, citations.

No model calls, no I/O — everything here is trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .models import Claim, OutlineSection, PlannedSubQuestion, RawClaim, SubQuestion, Verdict

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


def url_host(url: str) -> str:
    return canonical_url(url).split("/")[0]


# Near-duplicate thresholds are deliberately conservative: exact-match keys already catch
# case/punctuation variants; similarity only folds small insertions and token reorders.
# A false positive silently drops a genuinely new question/claim — worse than a rare dup.
QUESTION_SIMILARITY_THRESHOLD = 0.85
CLAIM_SIMILARITY_THRESHOLD = 0.9


def similarity(a: str, b: str) -> float:
    """Deterministic, embedding-free near-duplicate score in [0, 1] over two PRE-NORMALIZED
    strings (normalize_question/normalize_statement output): max of character-level
    SequenceMatcher ratio (catches small insertions) and token-set Jaccard (catches reorders)."""
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    return max(ratio, jaccard)


def assign_sub_question_ids(
    planned: list[PlannedSubQuestion], *, wave: int, start_index: int = 1
) -> list[SubQuestion]:
    """Turn planner/gap-analyst output into identified SubQuestions; numbering continues across waves."""
    return [
        SubQuestion(id=f"sq-{start_index + i:02d}", question=p.question, rationale=p.rationale, wave=wave)
        for i, p in enumerate(planned)
    ]


def dedup_questions(planned: list[PlannedSubQuestion], seen_normalized: set[str]) -> list[PlannedSubQuestion]:
    """Drop questions already asked — exactly (normalized key) or nearly (similarity against
    every seen question). Mutates seen_normalized with the survivors."""
    fresh: list[PlannedSubQuestion] = []
    for p in planned:
        key = normalize_question(p.question)
        if not key or key in seen_normalized:
            continue
        if any(similarity(key, seen) >= QUESTION_SIMILARITY_THRESHOLD for seen in seen_normalized):
            continue
        seen_normalized.add(key)
        fresh.append(p)
    return fresh


@dataclass(frozen=True)
class CitationEntry:
    number: int
    url: str  # first original URL seen for this canonical source
    title: str


@dataclass(frozen=True)
class CitationMap:
    """Stable numbering of sources, built by code — models never invent citation numbers."""

    entries: tuple[CitationEntry, ...]
    number_by_canonical: dict[str, int]

    def number_for(self, claim: Claim) -> int:
        return self.number_by_canonical[canonical_url(claim.source_url)]


def build_citation_map(claims: list[Claim]) -> CitationMap:
    """Number sources by first appearance in the (ordered) claim list."""
    entries: list[CitationEntry] = []
    number_by_canonical: dict[str, int] = {}
    for c in claims:
        canon = canonical_url(c.source_url)
        if canon not in number_by_canonical:
            number = len(entries) + 1
            number_by_canonical[canon] = number
            entries.append(CitationEntry(number=number, url=c.source_url, title=c.source_title))
    return CitationMap(entries=tuple(entries), number_by_canonical=number_by_canonical)


def outline_digest(
    query: str,
    done_criteria: list[str],
    sub_questions: list[SubQuestion],
    claims: list[Claim],
    verdict_by_claim: dict[str, Verdict],
) -> str:
    """What the outline agent sees: id, statement, verdict, source title — no quotes."""
    by_sq: dict[str, list[Claim]] = {}
    for c in claims:
        by_sq.setdefault(c.sub_question_id, []).append(c)

    lines = [f"MAIN QUESTION: {query}", "", "DONE CRITERIA:"]
    lines += [f"- {d}" for d in done_criteria]
    lines += ["", "VERIFIED CLAIMS (grouped by sub-question, strongest evidence first):"]
    for sq in sub_questions:
        sq_claims = by_sq.get(sq.id, [])
        if not sq_claims:
            continue
        lines.append(f"\n{sq.id}: {sq.question}")
        # Rank-ordered but NEVER truncated: the outline validator requires supported claims
        # to be assigned to sections, so the outline agent must see every claim id.
        for c in rank_claims(sq_claims):
            verdict = verdict_by_claim[c.id].verdict if c.id in verdict_by_claim else "unverified"
            lines.append(f"  - {c.id} [{verdict}] {c.statement} (source: {c.source_title})")
    return "\n".join(lines)


def section_prompt(
    section: OutlineSection,
    claims: list[Claim],
    citations: CitationMap,
    verdict_by_claim: dict[str, Verdict],
    revision_guidance: str = "",
) -> str:
    """What a section-writer sees: this section's full claim records + fixed citation numbers.
    On a revise pass, the critic's guidance is appended for the writer to apply verbatim."""
    lines = [
        f"SECTION TITLE: {section.title}",
        f"SECTION GOAL: {section.goal}",
        "",
        "CLAIMS FOR THIS SECTION (cite with the given [n]; use ONLY these claims):",
    ]
    for c in claims:
        verdict = verdict_by_claim[c.id].verdict if c.id in verdict_by_claim else "unverified"
        n = citations.number_for(c)
        lines.append(f"\n{c.id} -> cite as [{n}]{'†' if verdict == 'unverifiable' else ''}")
        lines.append(f"  verdict: {verdict}")
        lines.append(f"  statement: {c.statement}")
        lines.append(f'  quote: "{c.supporting_quote}"')
        lines.append(f"  source: {c.source_title}")
    if revision_guidance.strip():
        lines += [
            "",
            "--- CRITIC FEEDBACK ON THE PREVIOUS DRAFT (apply directly; do not argue) ---",
            revision_guidance.strip(),
        ]
    return "\n".join(lines)


def critic_prompt(
    query: str,
    done_criteria: list[str],
    report_markdown: str,
    claims: list[Claim],
    verdict_by_claim: dict[str, Verdict],
    iteration: int,
) -> str:
    """What the critic sees: the acceptance criteria, the claims (for grounding checks), and the
    assembled draft. Claims are id + verdict + statement + source — no quotes (grounding is by id)."""
    lines = [
        f"MAIN QUESTION: {query}",
        f"Iteration: {iteration} ({'first critique' if iteration == 0 else 'this draft was already revised'})",
        "",
        "ACCEPTANCE CRITERIA (from the plan):",
    ]
    lines += [f"{i + 1}. {c}" for i, c in enumerate(done_criteria)] or ["(none specified)"]
    lines += ["", "CLAIMS THE WRITER WAS GIVEN (grounding reference):"]
    for c in claims:
        verdict = verdict_by_claim[c.id].verdict if c.id in verdict_by_claim else "unverified"
        lines.append(f"- {c.id} [{verdict}] {c.statement} (source: {c.source_title})")
    lines += ["", "DRAFT TO GRADE:", "```markdown", report_markdown, "```", "", "Return your verdict now."]
    return "\n".join(lines)


def group_claims_by_url(claims: list[Claim]) -> dict[str, list[Claim]]:
    """Group claims by canonical URL for per-source verification. The dict key is the first
    original URL seen for that canonical form (verifiers need a real fetchable URL)."""
    canonical_to_original: dict[str, str] = {}
    grouped: dict[str, list[Claim]] = {}
    for c in claims:
        canon = canonical_url(c.source_url)
        original = canonical_to_original.setdefault(canon, c.source_url)
        grouped.setdefault(original, []).append(c)
    return grouped


# Per-sub-question claim listing cap inside digests. Claims beyond the cap are counted, not
# shown — they always remain in the RunRecord and the report. When the cap binds, the listing
# is rank-ordered and domain-capped so what survives is the best cross-source evidence.
DIGEST_CLAIMS_PER_SQ = 8
DIGEST_MAX_PER_DOMAIN = 3
# known_so_far_brief stays under ~600 tokens so it never crowds a researcher's own context.
BRIEF_CHAR_BUDGET = 2400


def central_digest(
    query: str,
    done_criteria: list[str],
    sub_questions: list[SubQuestion],
    claims: list[Claim],
    summaries_by_sq: dict[str, str],
    notes_by_sq: dict[str, str],
    failed_sq_ids: set[str],
    *,
    current_wave: int,
) -> str:
    """The evolving central workspace: rebuilt from typed state every round, never appended to.
    Per sub-question: the researcher's own summary, claim counts, and a capped claim listing —
    never quotes, never full URLs. Ends with a code-computed NEW THIS WAVE section so a reader
    can judge the marginal value of the last wave at a glance."""
    by_sq: dict[str, list[Claim]] = {}
    for c in claims:
        by_sq.setdefault(c.sub_question_id, []).append(c)

    lines: list[str] = [f"MAIN QUESTION: {query}", "", "DONE CRITERIA:"]
    lines += [f"- {d}" for d in done_criteria]
    lines += ["", f"RESEARCH WORKSPACE (wave {current_wave} just completed):"]
    for sq in sub_questions:
        sq_claims = by_sq.get(sq.id, [])
        domains = sorted({canonical_url(c.source_url).split("/")[0] for c in sq_claims})
        status = " [RESEARCHER FAILED]" if sq.id in failed_sq_ids else ""
        lines.append(f"\n{sq.id} (wave {sq.wave}){status}: {sq.question}")
        if summary := summaries_by_sq.get(sq.id, "").strip():
            lines.append(f"  summary: {summary}")
        lines.append(f"  claims: {len(sq_claims)} | distinct sources: {len(domains)}")
        shown = sq_claims
        if len(shown) > DIGEST_CLAIMS_PER_SQ:
            shown = cap_per_domain(rank_claims(sq_claims), DIGEST_MAX_PER_DOMAIN)[:DIGEST_CLAIMS_PER_SQ]
        for c in shown:
            corroborated = f" (x{c.corroborations + 1})" if c.corroborations else ""
            new = " [NEW]" if c.wave == current_wave else ""
            lines.append(f"  - [{c.confidence}]{new} {c.statement}{corroborated}")
        if (hidden := len(sq_claims) - len(shown)) > 0:
            lines.append(f"  (+{hidden} more claims not shown)")
        if notes := notes_by_sq.get(sq.id, "").strip():
            lines.append(f"  researcher notes: {notes}")

    fresh = [c for c in claims if c.wave == current_wave]
    lines += ["", f"NEW THIS WAVE (wave {current_wave}): {len(fresh)} new claim(s)"]
    for c in fresh[:DIGEST_CLAIMS_PER_SQ]:
        lines.append(f"- {c.statement} ({c.sub_question_id})")
    if len(fresh) > DIGEST_CLAIMS_PER_SQ:
        lines.append(f"(+{len(fresh) - DIGEST_CLAIMS_PER_SQ} more)")
    return "\n".join(lines)


def known_so_far_brief(
    sub_questions: list[SubQuestion],
    summaries_by_sq: dict[str, str],
    claims: list[Claim],
) -> str:
    """A compact what-we-already-know brief injected into later-wave researcher prompts,
    built from the per-branch summaries (falling back to claim counts). Hard-capped at
    ~600 tokens so it informs the researcher without crowding their own work."""
    counts: dict[str, int] = {}
    for c in claims:
        counts[c.sub_question_id] = counts.get(c.sub_question_id, 0) + 1

    entries: list[str] = []
    for sq in sub_questions:
        n = counts.get(sq.id, 0)
        summary = summaries_by_sq.get(sq.id, "").strip()
        if not summary:
            summary = "no summary; see claim count" if n else "nothing established"
        entries.append(f"- {sq.question} ({n} claim(s)): {summary}")
    if not entries:
        return ""

    header = "ALREADY ESTABLISHED by earlier waves (do not re-research this; target the gap):"
    body = "\n".join([header, *entries])
    if len(body) > BRIEF_CHAR_BUDGET:  # first squeeze each summary, then drop oldest entries
        entries = [e if len(e) <= 200 else e[:197] + "..." for e in entries]
        while entries and len("\n".join([header, *entries])) > BRIEF_CHAR_BUDGET - 30:
            entries.pop(0)
        body = "\n".join([header, "(earliest findings omitted for space)", *entries])
    return body


def gap_digest(
    query: str,
    done_criteria: list[str],
    sub_questions: list[SubQuestion],
    claims: list[Claim],
    summaries_by_sq: dict[str, str],
    notes_by_sq: dict[str, str],
    failed_sq_ids: set[str],
    *,
    current_wave: int,
) -> str:
    """What the gap analyst sees: the central workspace plus the already-asked-questions tail."""
    workspace = central_digest(
        query,
        done_criteria,
        sub_questions,
        claims,
        summaries_by_sq,
        notes_by_sq,
        failed_sq_ids,
        current_wave=current_wave,
    )
    lines = [workspace, "", "ALREADY-ASKED QUESTIONS (follow-ups must NOT restate these):"]
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
    """Assign claim ids and fold duplicates into ``corroborations``. Same-source duplicates
    (exact normalized statement, or near-duplicate wording) are dropped — they add nothing.
    CROSS-source near-duplicates are kept (a distinct source is distinct citation value), but
    both sides' ``corroborations`` increment, making the counter a real cross-source signal.
    Returns only the new claims; numbering continues from ``existing``."""
    index = {(canonical_url(c.source_url), normalize_statement(c.statement)): c for c in existing}
    new_claims: list[Claim] = []
    next_n = len(existing) + 1
    for rc in raw:
        canon, norm = canonical_url(rc.source_url), normalize_statement(rc.statement)
        if (canon, norm) in index:
            index[(canon, norm)].corroborations += 1
            continue
        same_source_dup = False
        cross_source_matches: list[Claim] = []
        for (c_canon, c_norm), c in index.items():
            if similarity(norm, c_norm) < CLAIM_SIMILARITY_THRESHOLD:
                continue
            if c_canon == canon:
                c.corroborations += 1
                same_source_dup = True
                break
            cross_source_matches.append(c)
        if same_source_dup:
            continue
        claim = Claim(
            id=f"c-{next_n:03d}",
            sub_question_id=sub_question_id,
            wave=wave,
            date_accessed=date_accessed,
            **rc.model_dump(),
        )
        for match in cross_source_matches:
            match.corroborations += 1
            claim.corroborations += 1
        index[(canon, norm)] = claim
        new_claims.append(claim)
        next_n += 1
    return new_claims


_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def rank_claims(claims: list[Claim]) -> list[Claim]:
    """Order for digest listings: confidence tier, then cross-source corroboration, then
    earliest wave. Stable, so equal claims keep their gathering order."""
    return sorted(claims, key=lambda c: (_CONFIDENCE_ORDER[c.confidence], -c.corroborations, c.wave))


def cap_per_domain(claims: list[Claim], max_per_domain: int) -> list[Claim]:
    """Keep at most ``max_per_domain`` claims per source host (order-preserving), so one
    over-quoted domain cannot monopolize a digest listing."""
    counts: dict[str, int] = {}
    kept: list[Claim] = []
    for c in claims:
        host = url_host(c.source_url)
        if counts.get(host, 0) < max_per_domain:
            counts[host] = counts.get(host, 0) + 1
            kept.append(c)
    return kept
