<div align="center">

# 🔍 deepresearch

**A deep research agent that cites its sources — and checks them.**

Ask a question; get back a cited Markdown report whose every claim has been re-fetched from its source and verified. Built end-to-end on the [Pydantic stack](https://pydantic.dev): [Pydantic AI](https://pydantic.dev/docs/ai/) · [Logfire](https://pydantic.dev/docs/logfire/) · [Pydantic Evals](https://pydantic.dev/docs/ai/evals/) · [Pydantic AI Gateway](https://pydantic.dev/docs/ai/gateway/). Anthropic models only, no other services.

![CI](https://github.com/albertogrande/deep-research/actions/workflows/ci.yml/badge.svg) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Built on](https://img.shields.io/badge/built%20on-Pydantic%20AI-e520a0) ![Tests](https://img.shields.io/badge/tests-53%20offline-brightgreen)

</div>

---

Most open-source "deep research" tools stop at *retrieve and summarize*. **deepresearch adds the step they skip: adversarial verification** — it re-fetches every cited page and judges each claim against what the page actually says. Contradicted claims are dropped; unverifiable ones (paywalls, 403s) are kept but flagged. The [eval suite](#evals) exists to prove whether that step earns its cost.

```
                   ┌──────────► Gap Analyst ──── follow-ups ────┐   iterate until
                   │                                            ▼   saturated / capped
   Planner ──► Researchers (parallel, server-side web search + fetch) ──► next wave …
     (acceptance     │
      criteria)      ▼
           Verifier  ── re-fetches every cited source, judges every claim ──►
                   │
                   ▼
           Synthesizer ── outline → sections, code-numbered [n] citations ──┐
                   │                                                         │
                   ▼                                                         │ revise (≤2×,
           Critic ── grades vs the plan's acceptance criteria → ship | revise ┘  guidance-driven)
                   │
                   ▼  ship
           report.md + run.json
```

## Highlights

- **🔬 Claim-level verification** — the differentiator. Each claim is re-checked against its source; verdicts (`supported` / `partial` / `unsupported` / `unverifiable`) drive what reaches the report.
- **🌊 Iterative, not one-shot** — a gap analyst reviews each wave and asks targeted follow-ups until the question is saturated (bounded by depth, budget, and dedup).
- **⚖️ Critic gate** — the planner sets acceptance criteria; after synthesis a critic grades the draft against them (coverage, grounding, redundancy, date/tone discipline) and sends it back for up to 2 guidance-driven revise passes. The outline and citation numbering stay fixed across revisions.
- **🧾 Typed end to end** — every hop is a Pydantic model with validators; the model writes prose, **code owns the citation numbers** (they can't drift).
- **💸 Deterministic cost control** — structural caps → per-call `UsageLimits` → a priced budget checkpointed between stages, with the Gateway spend cap as backstop. A failed run still writes its artifacts.
- **🔭 Observable** — one `logfire.instrument_pydantic_ai()` turns a whole run into a single trace tree (`plan → wave n → gap → verification → synthesis → critic`); each `run.json` carries its Logfire trace id.
- **📊 Measured** — a Pydantic Evals suite with objective metrics + LLM judges, and a **verifier on/off A/B experiment**.

## Quickstart

```bash
uv sync
cp .env.example .env          # add your keys (see Requirements)
uv run deepresearch "What are the main approaches to LLM hallucination detection?"
```

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
deepresearch "your question" [--depth quick|standard|deep] [--max-cost USD] [--no-verify]
```

| Depth | Waves | Synthesis model | Default cap |
|---|---|---|---|
| `quick` | 1 | Sonnet 4.6 | $0.50 |
| `standard` | 2 | Sonnet 5 | $2.00 |
| `deep` | up to 4 | Opus 4.8 | $8.00 |

Exit codes: `0` ok · `1` fatal · `2` synthesis fell back to a claims dump · `3` spend refusal · `130` interrupted.

<details>
<summary>All flags</summary>

| Flag | Effect |
|---|---|
| `-d, --depth` | `quick` · `standard` · `deep` (default `standard`) |
| `-o, --output` | output directory (default `runs/`) |
| `--max-cost` | USD cap for this run (overrides the profile default) |
| `--routing` | `gateway` · `direct` · `split` (server-tool roles direct, rest via gateway) |
| `--no-verify` | skip claim verification |
| `--json` | print `run.json` to stdout (scriptable) |
| `--plain` | line-based progress, no live UI |
| `-q, --quiet` | no progress output |

Per-role model overrides are env vars, e.g. `DEEPRESEARCH_MODELS__SYNTHESIZER=claude-opus-4-8`.

</details>

## Evals

```bash
uv run python -m evals.run_evals                  # 8-case suite (~$2.50) + LLM judges
uv run python -m evals.experiments.verifier_ab    # the flagship: verifier on vs off
```

Eight question categories (factual, multi-hop, time-sensitive, numeric, contested, niche-technical, survey, false-premise). Objective evaluators — citation coverage, citation integrity, URL resolution, verified-claim rate, unverifiable rate, unsupported-leakage, duration — plus three LLM judges (completeness, faithfulness, premise-handling). With `LOGFIRE_TOKEN` set, every run lands as a named experiment in Logfire.

<!-- A/B RESULTS: paste the verifier_ab table here after the first live run -->

## Development

```bash
uv run pytest                            # 53 offline tests — TestModel/FunctionModel, no API calls, no cost
RUN_LIVE_TESTS=1 uv run pytest -m live   # ~$0.05 live smoke (also answers the gateway question)
uv run python scripts/spike_gateway_server_tools.py   # run once per new environment
```

Architecture rules are in [CLAUDE.md](CLAUDE.md); the honest build log — every learning, dead end, and stack dev-ex note, from the project's origin story onward — is in **[JOURNAL.md](JOURNAL.md)**.

<details>
<summary>Known v1 limitations</summary>

No resume/checkpointing · exact-match question dedup only · sequential section writing · stage-level (not token-level) streaming · per-role model overrides are env-only · online evals not yet wired. See JOURNAL.md for the reasoning behind each.

</details>

## License

[MIT](LICENSE)
