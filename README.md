# deepresearch

A deep research agent system built end-to-end on the **Pydantic stack** — [Pydantic AI](https://pydantic.dev/docs/ai/) agents, [Logfire](https://pydantic.dev/docs/logfire/) observability, [Pydantic Evals](https://pydantic.dev/docs/ai/evals/), and the [Pydantic AI Gateway](https://pydantic.dev/docs/ai/gateway/) — using only Anthropic models and zero other services.

```
                 ┌────────────► Gap Analyst ──── follow-ups ────┐  (iterate until saturated)
                 │                                              ▼
Planner ──► wave 1: Researchers (parallel, server-side WebSearch + WebFetch) ──► wave 2 …
                 │
                 ▼
        Verifier (re-fetches EVERY cited source, judges every claim against it)
                 │
                 ▼
        Synthesizer (outline → sections, fixed [n] citations) ──► report.md + run.json
```

**The bet this project tests:** claim-level adversarial verification — re-fetching every cited source and checking each claim against what the page actually says — is the step most open-source deep-research clones skip. The eval suite exists to measure whether it earns its cost (`evals/experiments/verifier_ab.py`).

Built as a learning-in-public weekend project; the full build log — learnings, stack dev-ex notes, issues, limitations, and the project's origin story — lives in **[JOURNAL.md](JOURNAL.md)**.

## Quickstart

```bash
uv sync
cp .env.example .env    # add your keys (see below)
uv run deepresearch "What are the main approaches to LLM hallucination detection?"
```

You get a live progress UI (waves, researchers, verification pass rate, cost ticker), then `runs/<timestamp>-<slug>/report.md` (cited, with limitations and references) and `run.json` (the full typed audit trail: plan, claims, verdicts, usage, cost).

```
Usage: deepresearch QUESTION [--depth quick|standard|deep] [--max-cost USD]
                    [--routing gateway|direct|split] [--no-verify]
                    [--json] [--plain] [--quiet] [-o DIR]
```

Exit codes: `0` ok · `1` fatal · `2` synthesis fell back to a claims dump · `3` provider/gateway spend refusal · `130` interrupted.

## Keys and services

| What | Why | Required? |
|---|---|---|
| `PYDANTIC_AI_GATEWAY_API_KEY` | All model calls via the [Gateway](https://pydantic.dev/docs/ai/gateway/) (BYOK = free), giving spend caps + per-model cost tracking | default routing |
| `ANTHROPIC_API_KEY` | Direct Anthropic access (`--routing direct`, or `split`) | fallback |
| `LOGFIRE_TOKEN` | Full tracing of every run in [Logfire](https://pydantic.dev/docs/logfire/) (free tier: 10M spans/mo) | optional, recommended |

**Costs real money:** web search is $10/1k searches; a `standard` run is roughly **$0.55–0.90** with the default $2 cap. Depth profiles: `quick` (1 wave, Haiku-heavy, cap $0.50) · `standard` (2 waves, Sonnet planning + synthesis, cap $2) · `deep` (up to 4 waves, Opus synthesis, cap $8).

## What makes it interesting

- **Typed at every boundary.** `ResearchPlan → Findings → Claim → GapAnalysis → SourceVerification → Outline → RunRecord` are all Pydantic models; every agent has an `output_type` plus semantic output validators (`ModelRetry`) for rules schemas can't express ("`fetch_ok=false` ⇒ every verdict `unverifiable`", "an outline may not invent claim ids or drop >30% of supported ones").
- **Wave-based iteration, not single-pass fan-out.** A gap analyst reviews a code-built digest after each wave and either declares saturation or emits targeted follow-ups — bounded by profile, budget, and question dedup.
- **Verification with honest failure modes.** `unsupported` (source contradicts the claim) is excluded from the report; `unverifiable` (403/paywall) stays with a `†` marker and a limitations entry. Skipped verdicts are backfilled by code — nothing passes silently.
- **Models write prose; code owns the numbers.** Citation `[n]` markers are precomputed, and the References/Limitations blocks are assembled deterministically — numbering cannot drift (locked by a golden-file test).
- **Deterministic cost control, three layers.** Structural caps (waves, sub-questions, `max_uses`, semaphores) → per-run `UsageLimits` → a priced `Budget` checkpointed between stages, with the Gateway spend cap as the backstop. Failed runs still flush `run.json` and a claims-dump fallback report.
- **Observability end to end.** One `logfire.instrument_pydantic_ai()` + orchestrator spans (`plan → wave n → gap analysis → verification → synthesis`) make a full research run one readable trace tree.

## Evals

```bash
uv run python -m evals.run_evals                    # 8-case suite (~$2.50, live web) + LLM judges
uv run python -m evals.run_evals --no-judges        # objective metrics only
uv run python -m evals.experiments.verifier_ab     # the flagship A/B: verifier on vs off
```

8 question categories (factual, multi-hop, time-sensitive, numeric, contested, niche-technical, survey, false-premise). Objective evaluators: citation coverage, URL resolution (plain httpx), verified-claim rate, unverifiable rate, unsupported-leakage assertion, duration. Judges (explicitly Anthropic — the library default judge is OpenAI): completeness, faithfulness, premise handling. With `LOGFIRE_TOKEN` set, every eval run lands as a named experiment in Logfire.

<!-- A/B RESULTS: paste the verifier_ab table here after the first live run -->

## Development

```bash
uv run pytest                      # 47 offline tests — TestModel/FunctionModel, no API calls
RUN_LIVE_TESTS=1 uv run pytest -m live   # ~$0.05 live smoke (also answers the gateway question)
uv run python scripts/spike_gateway_server_tools.py  # gateway/server-tools spike, run once per env
```

Architecture rules live in [CLAUDE.md](CLAUDE.md); the honest history of what worked and what didn't is in [JOURNAL.md](JOURNAL.md).

### Known v1 limitations

No resume/checkpointing · exact-match question dedup only · sequential section writing · stage-level (not token-level) streaming UI · per-role model overrides are env-only · cost model ignores prompt-cache pricing · online evals not wired up. See journal entries for reasoning.

## License

[MIT](LICENSE)
