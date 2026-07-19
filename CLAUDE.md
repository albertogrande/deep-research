# deepresearch — repo conventions

Open-source deep research agent system on the Pydantic stack (Pydantic AI + Logfire +
Pydantic Evals + Pydantic AI Gateway). Anthropic models only. CLI-first.

## The Builder Journal (non-negotiable)

`JOURNAL.md` is a first-class deliverable of this project. **Before every push, append a dated
entry** covering: what was built, learnings/insights about the stack, issues hit and their
workarounds, limitations knowingly accepted, dev-ex impressions (good and bad), and cost
observations. Newest entries on top; entry 0 (origin story) stays at the bottom. Do not rewrite
history in old entries — corrections go in new entries.

## Architecture rules

- `src/deepresearch/agents/` contains **only** agent definitions, prompts, and output
  validators — no control flow.
- `orchestrator.py` owns all sequencing, concurrency, budget enforcement, and error policy
  (via `classify_error`). Agents never decide policy.
- `digest.py` is pure functions only. `artifacts.py` keeps report *assembly* pure (no model
  calls, golden-testable); the only side effects are the thin `write_*`/`create_run_dir`
  wrappers that form the file-I/O boundary.
- `models.py` is the single shared vocabulary (Claim, Findings, GapAnalysis, Verdict,
  Critique, RunRecord…) used by agents, orchestrator, artifacts, and evals alike.
- Model strings are never spelled outside `config.resolve_model()` — routing
  (`gateway` / `direct` / `split`) and per-role model selection live there only.
- The report's references/citation numbering is assembled by code, never written by a model.

## Commands

- `uv sync` — install everything (Python ≥3.11).
- `uv run pytest` — offline test suite (TestModel/FunctionModel; no API calls, no cost).
- `RUN_LIVE_TESTS=1 uv run pytest -m live` — live smoke tests (~$0.05; needs keys).
- `uv run deepresearch "question" --depth quick|standard|deep` — a research run.
- `uv run python scripts/spike_gateway_server_tools.py` — verify server-side web tools work
  through the Gateway (run once per new environment; record the answer in the journal).
- `uv run python -m evals.run_evals` — eval suite (costs ~$2.50; never wire into CI).
- `uv run python -m evals.experiments.verifier_ab` — the verifier on/off A/B (~$5).

## Environment

Copy `.env.example` → `.env`. Live runs need `ANTHROPIC_API_KEY` (routing=direct) or
`PYDANTIC_AI_GATEWAY_API_KEY` (routing=gateway, the default). `LOGFIRE_TOKEN` is optional but
the whole point — set it. Costs money: web search $10/1k searches; a standard run ≈ $0.55–0.90.

## Testing conventions

- Offline tests use `Agent.override(model=TestModel()/FunctionModel(...))` — never hit the
  network, never require keys. CI runs only these.
- Live tests are `@pytest.mark.live` and skipped unless `RUN_LIVE_TESTS=1`.
- Report rendering has golden-file tests; update goldens deliberately, never by regenerating
  blindly.
