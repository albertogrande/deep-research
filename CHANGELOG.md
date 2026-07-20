# Changelog

All notable changes to deepresearch. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/) (pre-1.0: minor = features, patch = fixes).

## [Unreleased]

### Added
- Per-branch compression: researchers summarize their own findings; an evolving central
  workspace digest (rebuilt from typed state each wave) with a NEW THIS WAVE section feeds
  the gap analyst, and later-wave researchers get an ALREADY ESTABLISHED brief.
- Context hygiene: embedding-free semantic dedup for questions and claims, cross-source
  corroboration tracking, claim ranking and per-domain caps in digest listings.
- Prompt caching per role (instructions + tool definitions for researchers/verifiers,
  message-level for the synthesizer) and adaptive/budgeted thinking on reasoning roles;
  cache read/write tokens surfaced in `run.json` and the CLI summary.
- Transport-level retries: shared tenacity-backed httpx client honouring Retry-After,
  with provider SDK retries disabled (no double-retry).
- Predictive budget guards: wave affordability gate (skip a wave the remaining budget can't
  cover) and mid-verification stop with explicit skip verdicts.
- Checkpoint/resume: stage-level `checkpoint.json`, `--resume runs/<id>`, spend restored
  against the original cap.
- Human-in-the-loop `--interactive`: clarify-first questions and an editable plan gate at
  the spend boundary.
- `--html` export (standalone, dark-mode-aware page derived from the report markdown).
- MCP server of the pipeline (`deepresearch-mcp`, `mcp` extra): one `deep_research` tool.
- Evals: FACT-style citation accuracy judge, RACE-style rubric, sentence-level citation
  density, position-swapped pairwise experiment, offline behavioral span evals in CI,
  `--depth` flag for the eval suite.
- Repo: AGENTS.md as the single source of truth, committed `.claude/` (settings, journal
  push-hook, skills), llms.txt, claude-code-action workflow, CI matrix (3.11–3.13) with
  pyright + coverage, PyPI trusted-publishing release workflow, `py.typed`, library exports.

### Fixed
- `genai-prices` declared as a direct dependency (was only transitively available).
- Offline test suite made hermetic: provider credentials are stripped per test, so an
  un-overridden agent can never silently hit the network.

## [0.1.0] - 2026-07-19

Initial public state: plan → parallel research waves with gap analysis → per-source
adversarial claim verification → two-stage synthesis with critic gate → cited report;
deterministic layered budget enforcement; Rich Live CLI with exit-code contract; offline
test suite; live eval suite with verifier A/B experiment; Logfire tracing throughout.
