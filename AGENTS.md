# deepresearch — agent & contributor guide

Open-source deep research agent system on the Pydantic stack (Pydantic AI + Logfire +
Pydantic Evals + Pydantic AI Gateway). Anthropic models only. CLI-first.

This file is the single source of truth for working in this repo (CLAUDE.md points here).
Facts live here; procedures live in `.claude/skills/`.

## The Builder Journal (non-negotiable)

`JOURNAL.md` is a first-class deliverable of this project. **Before every push, append a dated
entry** covering: what was built, learnings/insights about the stack, issues hit and their
workarounds, limitations knowingly accepted, dev-ex impressions (good and bad), and cost
observations. Newest entries on top; entry 0 (origin story) stays at the bottom. Do not rewrite
history in old entries — corrections go in new entries. A PreToolUse hook
(`.claude/hooks/check-journal-before-push.sh`) blocks `git push` when the outgoing commits
don't touch JOURNAL.md; format details in `.claude/skills/journal-entry/`.

## Architecture rules

- `src/deepresearch/agents/` contains **only** agent definitions, prompts, and output
  validators — no control flow. (This includes the clarifier: it runs on the planner's model
  and bills the planner ledger — no separate role.)
- `orchestrator.py` owns all sequencing, concurrency, budget enforcement (including the wave
  affordability gate and mid-verification stop), checkpoint writes, HITL gate placement, and
  error policy (via `classify_error`). Agents never decide policy.
- `digest.py` is pure functions only — digests, dedup/similarity, ranking, domain caps,
  citation numbering, query clarification folding.
- `artifacts.py` keeps report *assembly* pure (markdown and the HTML derived from it — both
  golden-testable); the only side effects are the thin `write_*`/`load_checkpoint`/
  `create_run_dir` wrappers that form the file-I/O boundary.
- `models.py` is the single shared vocabulary (Claim, Findings, GapAnalysis, Verdict,
  Critique, Checkpoint, RunRecord…) used by agents, orchestrator, artifacts, and evals alike.
- Model knowledge lives in `config.py` only: strings/routing in `resolve_model` (the label
  that lands in RunRecord), per-role caching/thinking in `role_model_settings`, retrying
  transports in `model_for_run`. Model names are never spelled anywhere else.
- `interaction.py` defines the HITL Protocol; UIs implement it (CLI: `ConsoleInteraction`).
  The orchestrator never knows what UI sits behind the hooks.
- The report's references/citation numbering is assembled by code, never written by a model.
  `report.html` is always derived from the assembled markdown, never assembled separately.
- Judge agents for evals live in `evals/`, not in `src/deepresearch/agents/`.

## Commands

- `uv sync` — install everything (Python ≥3.11). `uv sync --extra mcp` adds the MCP server.
- `uv run pytest` — offline test suite (TestModel/FunctionModel; hermetic: an autouse fixture
  strips provider credentials so nothing can hit the network). CI runs only these.
- `RUN_LIVE_TESTS=1 uv run pytest -m live` — live smoke tests (~$0.05; needs keys).
- `uv run deepresearch "question" --depth quick|standard|deep` — a research run.
  Also: `--interactive/-i` (clarify + plan gate), `--resume runs/<id>`, `--html`.
- `uv run deepresearch-mcp` — the MCP server (stdio; needs the `mcp` extra).
- `uv run python scripts/spike_gateway_server_tools.py` — verify server-side web tools work
  through the Gateway (run once per new environment; record the answer in the journal).
- `uv run python -m evals.run_evals` — eval suite (~$2.50 quick / ~$5 standard; never in CI).
- `uv run python -m evals.experiments.verifier_ab` — the verifier on/off A/B (~$5).
- `uv run python -m evals.experiments.pairwise A_DIR B_DIR` — pairwise report judging.
  Cost details and gating rules: `.claude/skills/run-evals/`.

## Environment

Copy `.env.example` → `.env`. Live runs need `ANTHROPIC_API_KEY` (routing=direct) or
`PYDANTIC_AI_GATEWAY_API_KEY` (routing=gateway, the default). `LOGFIRE_TOKEN` is optional but
the whole point — set it. Costs money: web search $10/1k searches; a standard run ≈ $0.55–0.90.

## Testing conventions

- Offline tests use `Agent.override(model=TestModel()/FunctionModel(...))` — never hit the
  network, never require keys. An autouse conftest fixture deletes provider credentials so an
  un-overridden agent fails fast instead of silently making a real call (see journal 20).
- Live tests are `@pytest.mark.live` and skipped unless `RUN_LIVE_TESTS=1`.
- Report rendering has golden-file tests (markdown inline, HTML in `tests/goldens/`); update
  goldens deliberately, never by regenerating blindly.
- Behavioral shape assertions use `HasMatchingSpan` over the scripted pipeline's span tree
  (`tests/test_behavior_evals.py`) — call `setup_telemetry()` before `dataset.evaluate`.
- To test interrupt handling, raise a custom `BaseException` subclass, never a literal
  `KeyboardInterrupt` (asyncio special-cases KI and it aborts the pytest event loop).

## Cost discipline

Every JOURNAL entry states what the session cost. Live spend needs explicit user sign-off;
development and CI are $0 by construction. The cheapest real run is
`--depth quick --no-verify --max-revise 0` (≈ $0.24).
