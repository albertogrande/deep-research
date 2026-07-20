<div align="center">

# 🔍 deepresearch

**A deep research agent that cites its sources — and checks them.**

Ask a question; get back a cited Markdown report whose every claim has been re-fetched from its source and verified. Built end-to-end on the [Pydantic stack](https://pydantic.dev): [Pydantic AI](https://pydantic.dev/docs/ai/) · [Logfire](https://pydantic.dev/docs/logfire/) · [Pydantic Evals](https://pydantic.dev/docs/ai/evals/) · [Pydantic AI Gateway](https://pydantic.dev/docs/ai/gateway/). Anthropic models only, no other services.

![CI](https://github.com/albertogrande/deep-research/actions/workflows/ci.yml/badge.svg) ![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Built on](https://img.shields.io/badge/built%20on-Pydantic%20AI-e520a0) ![Tests](https://img.shields.io/badge/tests-91%20offline-brightgreen) ![Typed](https://img.shields.io/badge/pyright-clean-brightgreen)

</div>

---

Most open-source "deep research" tools stop at *retrieve and summarize*. **deepresearch adds the step they skip: adversarial verification** — it re-fetches every cited page and judges each claim against what the page actually says. Contradicted claims are dropped; unverifiable ones (paywalls, 403s) are kept but flagged. The [eval suite](#evals) exists to prove whether that step earns its cost.

```
   Clarifier (-i) ── 0-3 plan-changing questions ──► editable plan gate (pre-spend)
                   │
                   ┌──────────► Gap Analyst ──── follow-ups ────┐   iterate until saturated /
                   │     (sees the compressed workspace digest) ▼   capped / unaffordable
   Planner ──► Researchers (parallel, server-side web search + fetch) ──► next wave …
     (acceptance     │                            (wave ≥2 gets the ALREADY-ESTABLISHED brief)
      criteria)      ▼
           Verifier  ── re-fetches every cited source, judges every claim ──►
                   │
                   ▼
           Synthesizer ── outline → sections, code-numbered [n] citations ──┐
                   │                                                         │
                   ▼                                                         │ revise (≤2×,
           Critic ── grades vs the plan's acceptance criteria → ship | revise ┘  guidance-driven)
                   │
                   ▼  ship                        every stage checkpoints → --resume
           report.md (+ report.html) + run.json
```

## Highlights

- **🔬 Claim-level verification** — the differentiator. Each claim is re-checked against its source; verdicts (`supported` / `partial` / `unsupported` / `unverifiable`) drive what reaches the report.
- **🌊 Iterative with a compressed workspace** — a gap analyst reviews each wave through an evolving, code-built digest (per-branch researcher summaries, `NEW THIS WAVE` deltas, semantic dedup, cross-source corroboration, per-domain caps) and asks targeted follow-ups until marginal value runs out — the IterResearch-style technique with the best measured effect in the field.
- **🧑‍⚖️ Human-in-the-loop when you want it** — `--interactive` asks 0–3 clarifying questions *only when the answer would change the plan*, then lets you add/drop/edit sub-questions before a single researcher spends money.
- **⏯️ Interruptible** — every stage checkpoints; `--resume runs/<id>` continues where a run stopped, with prior spend still counted against the original cap.
- **⚖️ Critic gate** — the planner sets acceptance criteria; after synthesis a critic grades the draft against them and drives bounded, guidance-based revise passes (`--max-revise`, `0` = cheapest). The outline and citation numbering stay fixed across revisions.
- **🧾 Typed end to end** — every hop is a Pydantic model with validators; the model writes prose, **code owns the citation numbers** (they can't drift). `py.typed`, pyright-clean.
- **💸 Deterministic cost control** — structural caps → per-call `UsageLimits` → priced checkpoints → a *predictive* wave-affordability gate (a wave the remaining budget can't cover never launches), with the Gateway spend cap as backstop. Prompt caching and adaptive thinking are tuned per role in one config table. A failed run still writes its artifacts.
- **🔭 Observable** — one `logfire.instrument_pydantic_ai()` turns a whole run into a single trace tree; each `run.json` carries its Logfire trace id. Cache hit rates show up in the run summary.
- **📊 Measured** — Pydantic Evals suite with objective metrics, FACT-style **citation accuracy** and RACE-style quality judges, a **verifier on/off A/B**, a position-swapped pairwise harness — plus behavioral span evals that run offline in CI.
- **🔌 Not just a CLI** — importable library (`from deepresearch import run_research`) and an MCP server of itself (`deepresearch-mcp`) for Claude Desktop/Code.

## Quickstart

```bash
uv sync
cp .env.example .env          # add your keys (see Requirements)
uv run deepresearch "What are the main approaches to LLM hallucination detection?"
```

(After the first PyPI release this becomes `uvx deepresearch "question"` — no clone needed.)

You get a live progress view, then a run folder:

```
runs/20260719-...-llm-hallucination-detection/
├── report.md      # cited report: TL;DR, sections, limitations, references
└── run.json       # full audit trail: plan, claims, verdicts, usage, cost, timings
```

…and a summary:

```
run 20260719-...-llm-hallucination-detection
claims                34
verdicts              supported: 22 · partial: 6 · unverifiable: 6  (79% of judged supported)
waves                 2 (saturated)
searches              18
est. cost             $0.71
duration              94s
report                runs/…/report.md
```

## Requirements

| Variable | Purpose | Needed? |
|---|---|---|
| `PYDANTIC_AI_GATEWAY_API_KEY` | Route all calls through the [Gateway](https://pydantic.dev/docs/ai/gateway/) (BYOK = free) for spend caps + cost tracking | default routing |
| `ANTHROPIC_API_KEY` | Direct Anthropic access (`--routing direct` or `split`) | fallback |
| `LOGFIRE_TOKEN` | Full tracing in [Logfire](https://pydantic.dev/docs/logfire/) (free tier: 10M spans/mo) | optional, recommended |

Python ≥ 3.11 · [uv](https://docs.astral.sh/uv/). **Costs real money:** web search is $10/1k searches; a `standard` run ≈ **$0.55–0.90**.

## Usage

```bash
deepresearch "your question" [--depth quick|standard|deep] [--max-cost USD] [--no-verify] [--max-revise N]
```

| Depth | Waves | Synthesis model | Default cap |
|---|---|---|---|
| `quick` | 1 | Sonnet 4.6 | $0.50 |
| `standard` | 2 | Sonnet 5 | $2.00 |
| `deep` | up to 4 | Opus 4.8 | $8.00 |

Exit codes: `0` ok · `1` fatal · `2` synthesis fell back to a claims dump · `3` spend refusal · `130` interrupted.

**Minimum-cost run** (~$0.24, still a real cited report): `--depth quick --no-verify --max-revise 0`. At the `quick` tier the search cost is the floor (9 searches × $0.01 = $0.09); the revise loop is the biggest saveable lever, so `--max-revise 0` is where most of the savings come from.

**Interrupted?** Every run checkpoints after each stage — `deepresearch --resume runs/<id>` continues where it stopped, with the money already spent still counted against the original cap.

<details>
<summary>All flags</summary>

| Flag | Effect |
|---|---|
| `-d, --depth` | `quick` · `standard` · `deep` (default `standard`) |
| `-o, --output` | output directory (default `runs/`) |
| `--max-cost` | USD cap for this run (overrides the profile default) |
| `--routing` | `gateway` · `direct` · `split` (server-tool roles direct, rest via gateway) |
| `--no-verify` | skip claim verification |
| `--max-revise` | critic-driven revise passes (default 2; `0` disables the critic loop — cheapest) |
| `--resume` | continue an interrupted run from its `runs/<id>` directory (omit QUESTION) |
| `-i, --interactive` | clarifying questions + editable plan gate before any money is spent |
| `--html` | also render a standalone `report.html` (inline CSS, dark-mode aware) |
| `--json` | print `run.json` to stdout (scriptable) |
| `--plain` | line-based progress, no live UI |
| `-q, --quiet` | no progress output |

Per-role model overrides are env vars, e.g. `DEEPRESEARCH_MODELS__SYNTHESIZER=claude-opus-4-8`.

</details>

### Use from Claude (MCP)

deepresearch ships an MCP server of itself: one `deep_research(question, depth, max_cost)` tool
returning the cited report. Install the extra (`uv sync --extra mcp`) and register the stdio
command in Claude Desktop / Claude Code:

```json
{
  "mcpServers": {
    "deepresearch": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "deepresearch-mcp"],
      "cwd": "/path/to/deep-research"
    }
  }
}
```

## How it compares

| | **deepresearch** | gpt-researcher | open_deep_research | DeerFlow |
|---|---|---|---|---|
| Per-claim source re-verification | **✅ dedicated stage** | ❌ | ❌ | ❌ |
| Citation numbering | **code-owned** | model-written | model-written | model-written |
| Compressed research workspace | ✅ | ❌ | ✅ | partial |
| Editable plan gate / clarify | ✅ | partial | ✅ | ✅ |
| Checkpoint / resume | ✅ | ❌ | ❌ | ❌ |
| Deterministic $ budget + predictive gating | ✅ | ❌ | ❌ | ❌ |
| Eval suite in-repo (citation accuracy, A/B) | ✅ | ❌ | partial | ❌ |
| Offline CI (incl. behavioral span evals) | ✅ | partial | partial | partial |
| MCP server of itself | ✅ | ✅ | ❌ | ❌ |
| Providers | Anthropic only (by design) | many | many | many |

The bet this project makes: **verification and measurement beat breadth**. One provider,
one stack, every claim checked, and eval numbers published instead of implied.

## Evals

```bash
uv run python -m evals.run_evals                    # 8-case suite (~$2.50) + judges
uv run python -m evals.run_evals --depth standard   # evaluate the depth users actually run
uv run python -m evals.experiments.verifier_ab      # the flagship: verifier on vs off (~$5)
uv run python -m evals.experiments.pairwise A B     # win/tie/loss between two run dirs
```

Eight question categories (factual, multi-hop, time-sensitive, numeric, contested, niche-technical, survey, false-premise). Objective evaluators — citation coverage + sentence-level density, citation integrity, URL resolution, verified-claim rate, unverifiable rate, unsupported-leakage, duration — plus LLM judges: completeness, faithfulness, premise-handling, a RACE-style rubric (comprehensiveness/insight/readability), and FACT-style **citation accuracy** (sampled cited sentences judged for entailment against the claims' verbatim quotes — the metric where even top products only reach 78–90%). With `LOGFIRE_TOKEN` set, every run lands as a named experiment in Logfire.

<!-- A/B RESULTS: paste the verifier_ab table here after the first live run -->

<details>
<summary>Live validation checklist (~$9–12 total; run when ready to spend)</summary>

Everything in this repo was built and tested offline ($0). To validate live behavior and fill
the results placeholder above, run in order — stop at any budget line:

1. `uv run python scripts/spike_gateway_server_tools.py` then `RUN_LIVE_TESTS=1 uv run pytest -m live` — environment sanity (~$0.05).
2. One standard run: `uv run deepresearch "your question" --depth standard` — check the cache-hit row in the summary, thinking behavior in the Logfire trace, and wave gating (~$1).
3. `uv run python -m evals.run_evals` — full suite incl. citation accuracy (~$3).
4. `uv run python -m evals.experiments.verifier_ab` — the A/B (~$5).
5. Paste the A/B table over the placeholder above; add the citation-accuracy number and cache-hit rate to Highlights. Optionally render the demo GIF: `vhs docs/demo.tape` (one more quick run).

</details>

## Development

```bash
uv run pytest                            # 91 offline tests — hermetic: no API calls, no keys, no cost
uv run pyright && uv run ruff check .    # CI runs these on 3.11–3.13
RUN_LIVE_TESTS=1 uv run pytest -m live   # ~$0.05 live smoke (also answers the gateway question)
uv run python scripts/spike_gateway_server_tools.py   # run once per new environment
```

Repo rules, commands, and architecture invariants live in **[AGENTS.md](AGENTS.md)** (agent-
and human-readable; [llms.txt](llms.txt) has the source map). The honest build log — every
learning, dead end, and stack dev-ex note, from the origin story onward — is
**[JOURNAL.md](JOURNAL.md)**. Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

<details>
<summary>Known limitations</summary>

Sequential section writing (deliberate — parallel section-writing measurably produces
disjoint reports) · stage-level, not token-level, streaming · per-role model overrides are
env-only · similarity dedup is string-based, not embedding-based (deliberate: Anthropic-only
means no embedding endpoint, and it's free and deterministic) · live eval numbers not yet
published (the checklist above fills them in). See JOURNAL.md for the reasoning behind each.

</details>

## License

[MIT](LICENSE)
