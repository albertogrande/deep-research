"""Run artifacts: output directory, run.json, and report assembly.

The report's skeleton — title, TL;DR quote, section headings, Limitations, References — is
rendered by code. Models write section prose only, so citation numbering can never drift.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .digest import CitationMap
from .models import Checkpoint, Claim, Outline, RunRecord, SubQuestion, Verdict

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    s = _SLUG_STRIP.sub("-", text.casefold()).strip("-")
    return s[:max_len].rstrip("-") or "run"


def new_run_id(query: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{now:%Y%m%d-%H%M%S}-{slugify(query)}"


def create_run_dir(output_dir: str | Path, run_id: str) -> Path:
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_run_record(record: RunRecord, run_dir: Path) -> Path:
    path = run_dir / "run.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_checkpoint(checkpoint: Checkpoint, run_dir: Path) -> Path:
    path = run_dir / "checkpoint.json"
    path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_checkpoint(run_dir: Path) -> Checkpoint:
    path = run_dir / "checkpoint.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no checkpoint.json in {run_dir} — either the run finished (checkpoints are "
            "removed on success) or this is not a run directory"
        )
    return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))


def delete_checkpoint(run_dir: Path) -> None:
    (run_dir / "checkpoint.json").unlink(missing_ok=True)


def _references_block(citations: CitationMap, date_accessed: str) -> list[str]:
    lines = ["## References", ""]
    for e in citations.entries:
        lines.append(f"{e.number}. {e.title} — <{e.url}> (accessed {date_accessed})")
    return lines


def _limitations_block(limitations: list[str]) -> list[str]:
    if not limitations:
        return []
    lines = ["## Limitations", ""]
    lines += [f"- {lim}" for lim in limitations]
    return lines


def assemble_report(
    outline: Outline,
    section_bodies: list[str],
    citations: CitationMap,
    limitations: list[str],
    date_accessed: str,
    unverifiable_count: int = 0,
) -> str:
    """Pure assembly of the final Markdown report."""
    lines: list[str] = [f"# {outline.title}", "", f"> **TL;DR** — {outline.tldr}", ""]
    for section, body in zip(outline.sections, section_bodies, strict=True):
        lines += [f"## {section.title}", "", body.strip(), ""]
    if unverifiable_count:
        lines += ["*Citations marked with † could not be independently re-verified.*", ""]
    lines += _limitations_block(limitations)
    if limitations:
        lines.append("")
    lines += _references_block(citations, date_accessed)
    lines.append("")
    return "\n".join(lines)


def claims_dump_report(
    query: str,
    sub_questions: list[SubQuestion],
    claims: list[Claim],
    verdict_by_claim: dict[str, Verdict],
    citations: CitationMap,
    limitations: list[str],
    date_accessed: str,
    reason: str,
) -> str:
    """Fallback artifact when synthesis fails or is aborted: verified claims grouped by
    sub-question. Not a report, but the research still yields value."""
    lines = [
        f"# Research findings (unsynthesized): {query}",
        "",
        f"> Report synthesis was not completed ({reason}). "
        "Below are the verified claims gathered by the research waves.",
        "",
    ]
    by_sq: dict[str, list[Claim]] = {}
    for c in claims:
        by_sq.setdefault(c.sub_question_id, []).append(c)
    for sq in sub_questions:
        sq_claims = by_sq.get(sq.id, [])
        if not sq_claims:
            continue
        lines += [f"## {sq.question}", ""]
        for c in sq_claims:
            verdict = verdict_by_claim[c.id].verdict if c.id in verdict_by_claim else "unverified"
            dagger = "†" if verdict == "unverifiable" else ""
            lines.append(f"- {c.statement} [{citations.number_for(c)}]{dagger} *({verdict})*")
        lines.append("")
    lines += _limitations_block(limitations)
    if limitations:
        lines.append("")
    lines += _references_block(citations, date_accessed)
    lines.append("")
    return "\n".join(lines)


def write_report(text: str, run_dir: Path) -> Path:
    path = run_dir / "report.md"
    path.write_text(text, encoding="utf-8")
    return path
