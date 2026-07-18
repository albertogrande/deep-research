# deepresearch

A deep research agent system built end-to-end on the **Pydantic stack**: [Pydantic AI](https://pydantic.dev/docs/ai/) agents, [Logfire](https://pydantic.dev/docs/logfire/) observability, [Pydantic Evals](https://pydantic.dev/docs/ai/evals/), and the [Pydantic AI Gateway](https://pydantic.dev/docs/ai/gateway/). Anthropic models only; no other services.

```
Planner ──► wave 1: Researchers (parallel, server-side WebSearch+WebFetch) ──► Gap Analyst ──► wave 2 …
        └──────────► Verifier (re-fetches every cited source) ──► Synthesizer ──► report.md + run.json
```

The bet this project tests: **claim-level adversarial verification** — re-fetching every cited source and checking each claim against it — is the step most open-source deep-research clones skip, and Pydantic Evals is used to measure whether it earns its cost (verifier on/off A/B).

> 🚧 Under construction — built as a learning-in-public weekend project. Progress, learnings, and dev-ex notes live in [JOURNAL.md](JOURNAL.md), starting from the project's origin story.

## Quickstart

```bash
uv sync
cp .env.example .env   # add your keys
uv run deepresearch "What are the main approaches to LLM hallucination detection?" --depth standard
```

Requires: an Anthropic API key (or a Pydantic AI Gateway key with BYOK), optionally a Logfire token for traces. Costs real money: web search is $10/1k searches; a standard-depth run ≈ $0.55–0.90.
