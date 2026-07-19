from deepresearch.digest import (
    BRIEF_CHAR_BUDGET,
    DIGEST_CLAIMS_PER_SQ,
    assign_sub_question_ids,
    canonical_url,
    central_digest,
    dedup_questions,
    enrich_and_dedup_claims,
    known_so_far_brief,
    normalize_question,
)
from deepresearch.models import Claim, PlannedSubQuestion, RawClaim, SubQuestion


def _raw(statement: str, url: str) -> RawClaim:
    return RawClaim(
        statement=statement,
        supporting_quote="quote",
        source_url=url,
        source_title="title",
        confidence="high",
    )


def _claim(n: int, sq_id: str, wave: int, statement: str = "", url: str = "") -> Claim:
    return Claim(
        id=f"c-{n:03d}",
        sub_question_id=sq_id,
        wave=wave,
        date_accessed="2026-07-19",
        statement=statement or f"Fact number {n}.",
        supporting_quote="quote",
        source_url=url or f"https://example.com/{n}",
        source_title="title",
        confidence="high",
    )


def _sq(n: int, wave: int = 1, question: str = "") -> SubQuestion:
    return SubQuestion(id=f"sq-{n:02d}", question=question or f"Question {n}?", rationale="r", wave=wave)


def test_normalize_question_strips_interrogative_boilerplate():
    assert normalize_question("What is the capital of France?") == "capital of france"
    assert normalize_question("  How does MVCC work??") == "mvcc work"
    assert normalize_question("capital of France") == "capital of france"


def test_canonical_url_strips_noise():
    assert canonical_url("https://www.Example.com/a/b/?utm=x#frag") == "example.com/a/b"
    assert canonical_url("http://example.com/a/b") == "example.com/a/b"
    assert canonical_url("https://example.com/") == "example.com"


def test_dedup_questions_tracks_seen():
    seen: set[str] = set()
    first = dedup_questions([PlannedSubQuestion(question="What is X?", rationale="r")], seen)
    assert len(first) == 1
    again = dedup_questions(
        [
            PlannedSubQuestion(question="what is x", rationale="r2"),  # dup modulo normalization
            PlannedSubQuestion(question="What is Y?", rationale="r3"),
        ],
        seen,
    )
    assert [q.question for q in again] == ["What is Y?"]


def test_sub_question_ids_continue_numbering():
    a = assign_sub_question_ids([PlannedSubQuestion(question="q1", rationale="r")], wave=1)
    b = assign_sub_question_ids(
        [PlannedSubQuestion(question="q2", rationale="r")], wave=2, start_index=len(a) + 1
    )
    assert [sq.id for sq in a + b] == ["sq-01", "sq-02"]
    assert (a[0].wave, b[0].wave) == (1, 2)


def test_claim_dedup_folds_into_corroborations():
    existing = enrich_and_dedup_claims(
        [_raw("Paris is the capital of France.", "https://en.wikipedia.org/wiki/Paris")],
        sub_question_id="sq-01",
        wave=1,
        date_accessed="2026-07-18",
        existing=[],
    )
    assert [c.id for c in existing] == ["c-001"]

    new = enrich_and_dedup_claims(
        [
            # same statement + same canonical URL (www + trailing slash) -> corroboration
            _raw("Paris is the capital of France", "https://www.en.wikipedia.org/wiki/Paris/"),
            _raw("The Seine flows through Paris.", "https://en.wikipedia.org/wiki/Seine"),
        ],
        sub_question_id="sq-02",
        wave=1,
        date_accessed="2026-07-18",
        existing=existing,
    )
    assert [c.id for c in new] == ["c-002"]
    assert existing[0].corroborations == 1


def test_central_digest_marks_new_wave_and_shows_summaries():
    sqs = [_sq(1, wave=1), _sq(2, wave=2)]
    claims = [_claim(1, "sq-01", 1), _claim(2, "sq-02", 2)]
    out = central_digest(
        "Main?",
        ["done"],
        sqs,
        claims,
        {"sq-01": "Established the base fact."},
        {},
        set(),
        current_wave=2,
    )
    assert "RESEARCH WORKSPACE (wave 2 just completed):" in out
    assert "summary: Established the base fact." in out
    assert "[high] [NEW] Fact number 2." in out
    assert "[high] Fact number 1." in out  # wave-1 claim carries no NEW marker
    assert "NEW THIS WAVE (wave 2): 1 new claim(s)" in out
    assert "- Fact number 2. (sq-02)" in out


def test_central_digest_caps_per_sq_claim_listing():
    n_claims = DIGEST_CLAIMS_PER_SQ + 3
    claims = [_claim(i, "sq-01", 1) for i in range(1, n_claims + 1)]
    out = central_digest("Main?", [], [_sq(1)], claims, {}, {}, set(), current_wave=1)
    assert f"claims: {n_claims}" in out
    assert "(+3 more claims not shown)" in out
    assert f"Fact number {DIGEST_CLAIMS_PER_SQ}." in out
    assert f"Fact number {DIGEST_CLAIMS_PER_SQ + 1}." not in out.split("NEW THIS WAVE")[0]


def test_known_so_far_brief_content_and_fallbacks():
    sqs = [_sq(1, question="What is X?"), _sq(2, question="What is Y?")]
    claims = [_claim(1, "sq-01", 1)]
    out = known_so_far_brief(sqs, {"sq-01": "X is 42."}, claims)
    assert out.startswith("ALREADY ESTABLISHED")
    assert "- What is X? (1 claim(s)): X is 42." in out
    assert "- What is Y? (0 claim(s)): nothing established" in out
    assert known_so_far_brief([], {}, []) == ""


def test_known_so_far_brief_respects_char_budget():
    sqs = [_sq(i) for i in range(1, 40)]
    summaries = {f"sq-{i:02d}": "word " * 120 for i in range(1, 40)}
    out = known_so_far_brief(sqs, summaries, [])
    assert len(out) <= BRIEF_CHAR_BUDGET
    assert "ALREADY ESTABLISHED" in out
    assert "omitted for space" in out
    assert "- Question 39?" in out  # newest entries survive the squeeze
