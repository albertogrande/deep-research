# Security Policy

## Supported versions

The latest release and `main`. Pre-1.0, fixes land on `main` and ship in the next release.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/albertogrande/deep-research/security/advisories/new)
or email grande.temprado@gmail.com. You'll get an acknowledgment within a week.

## Scope notes for this project

- deepresearch executes **no code from the web** — researcher agents use Anthropic
  server-side web search/fetch; page content only ever becomes model context and typed,
  validated claim data.
- Reports render web-derived text into `report.md`/`report.html`. The HTML page contains no
  scripts, but treat rendered reports from untrusted questions as untrusted content.
- Prompt-injection via fetched pages is an inherent LLM-pipeline risk: the verifier stage
  re-checks claims against sources, and citation numbering is code-owned, but adversarial
  pages influencing prose remains possible. Findings that *escalate* this (e.g. injection
  that exfiltrates env secrets) are firmly in scope — report them.
- API keys live in `.env`/environment only; nothing in the repo or artifacts should ever
  contain them (`run.json` carries usage numbers, never credentials).
