## What & why

<!-- The change and the problem it solves. Link the issue if there is one. -->

## Checklist

- [ ] `uv run pytest` green (offline; no keys needed)
- [ ] `uv run ruff check . && uv run ruff format --check .` clean
- [ ] `uv run pyright` clean
- [ ] **JOURNAL.md entry appended** (what/learnings/issues/limitations/cost — see `.claude/skills/journal-entry/`)
- [ ] Goldens unchanged, or changed deliberately and eyeballed
- [ ] Architecture invariants respected (AGENTS.md) — policy in orchestrator, agents definition-only
- [ ] Live spend: state what validation cost, or that it was offline-only
