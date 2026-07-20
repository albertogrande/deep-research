# Contributing to deepresearch

Thanks for considering it. This project doubles as a public build-log of doing agent
engineering on the Pydantic stack, which shapes a few conventions you won't see elsewhere.

## Dev setup

```bash
uv sync                 # Python >= 3.11; installs everything incl. dev tools
uv run pytest           # offline suite — no API keys, no network, no cost
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

Optional: `uv run pre-commit install` for ruff on commit. Live anything needs keys —
see `.env.example` — and **costs real money**; nothing in the dev loop requires it.

## The rules that matter

1. **Offline tests stay offline.** They run with provider credentials stripped (autouse
   fixture), so an agent you forgot to `Agent.override(...)` fails fast instead of silently
   calling an API. If your test needs the network, it's `@pytest.mark.live`.
2. **The journal is part of the change.** Every push appends a dated entry to `JOURNAL.md`
   (what/learnings/issues/limitations/cost — see `.claude/skills/journal-entry/`). A git hook
   in `.claude/` enforces this for agent sessions; humans are on the honor system, and PRs
   are checked for it.
3. **Architecture invariants** live in [AGENTS.md](AGENTS.md) — agents define, the
   orchestrator decides, digests are pure, code owns citation numbers, model knowledge stays
   in `config.py`. PRs that move policy into agents will be asked to move it back.
4. **Goldens change deliberately.** If `tests/goldens/` or the inline report golden changes,
   the diff must be intentional and eyeballed — never regenerated blindly.
5. **Live spend is opt-in and stated.** If your PR was validated with live runs, say what it
   cost. If it wasn't, that's fine — say that instead.

## Pull requests

- Branch from `main`; keep PRs focused.
- CI must be green (3.11–3.13 matrix: ruff, pyright, pytest+coverage ≥80%).
- Include the JOURNAL.md entry in the same PR.
- `@claude` works on issues and PRs when the maintainer has enabled it.

## Releases

Maintainers only — the flow is documented in `.claude/skills/release/SKILL.md`
(version bump → CHANGELOG → tag → PyPI trusted publishing).
