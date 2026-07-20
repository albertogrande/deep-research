---
name: run-evals
description: Run the deepresearch eval suite or experiments (verifier A/B, pairwise). Use when asked to evaluate quality, produce eval numbers, or compare configurations. LIVE - costs real money and requires explicit user sign-off.
---

# Running evals

**Every command here spends real money. Get explicit user sign-off with the estimated cost
before running anything. Never wire any of these into CI.**

## Cost table

| Command | What it does | Approx. cost |
|---|---|---|
| `uv run python -m evals.run_evals --no-judges` | 8 cases, objective metrics only | ~$2.00 |
| `uv run python -m evals.run_evals` | + LLM judges (RACE rubric, citation accuracy) | ~$2.50–3.00 |
| `uv run python -m evals.run_evals --depth standard` | evaluate the depth users actually run | ~$5–6 |
| `uv run python -m evals.experiments.verifier_ab` | verifier on/off A/B, delta table | ~$5 |
| `uv run python -m evals.experiments.pairwise A B` | judge two existing run dirs | ~$0.15 |
| `uv run python -m evals.experiments.pairwise --live ...` | two live arms + judging | ~2× eval run |

## Procedure

1. Confirm the environment has keys (`PYDANTIC_AI_GATEWAY_API_KEY` or `ANTHROPIC_API_KEY`)
   and `LOGFIRE_TOKEN` (experiments land in the Logfire UI when set).
2. Name the experiment: `--name <something-greppable>` — it becomes the Logfire experiment id.
3. Run from the repo root; artifacts land under `runs-evals/<subdir>/`.
4. Record results in JOURNAL.md (numbers, cost, surprises) and, when they answer the README's
   `<!-- A/B RESULTS -->` placeholder or citation-accuracy claim, paste them there too.

## Offline (free) checks

`uv run pytest tests/test_behavior_evals.py` asserts pipeline shape via span evals at $0 —
prefer it for regression questions that don't need live quality numbers.
