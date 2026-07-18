"""Run artifacts: output directory layout and run.json. Report assembly lands in Phase 4."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .models import RunRecord

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
