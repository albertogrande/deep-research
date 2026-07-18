from deepresearch.digest import (
    assign_sub_question_ids,
    canonical_url,
    dedup_questions,
    enrich_and_dedup_claims,
    normalize_question,
)
from deepresearch.models import PlannedSubQuestion, RawClaim


def _raw(statement: str, url: str) -> RawClaim:
    return RawClaim(
        statement=statement,
        supporting_quote="quote",
        source_url=url,
        source_title="title",
        confidence="high",
    )


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
