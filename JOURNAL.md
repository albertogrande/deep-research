# Builder Journal — deepresearch

A running log of learnings, insights, issues, limitations, and developer-experience notes
from building a deep research agent system on the full Pydantic stack (Pydantic AI, Logfire,
Pydantic Evals, Pydantic AI Gateway). Newest entries at the top; entry 0 (the origin story)
stays at the bottom.

Convention: every working session appends an entry before pushing — what was built, what was
learned, what broke, what the stack made easy or hard, and what it cost.

---

## Entry 3 — 2026-07-18 — Phase 2: the wave loop — digests, dedup, budget, error policy

**Built:** gap analyst agent, code-built gap digest (statements + counts + notes, never quotes),
two-layer dedup (questions and claims), `classify_error` (transient / degradable / budget-fatal /
fatal) with one-retry-on-transient, budget checkpoints at wave boundaries, and 5 scripted
wave-loop policy tests (saturation stop, max-waves stop, dedup-empty stop, budget stop,
transient retry).

**Learnings & dev-ex notes:**

- **`TestModel` generates real token usage numbers**, which means budget-enforcement tests work
  without any mocking of the accounting: set `max_cost=0.000001`, run the pipeline, watch the
  checkpoint trip. The deterministic-budget design paid off immediately in testability.
- **`FunctionModel` + prompt-sniffing is the pattern for per-unit scripting** in fan-out tests:
  the scripted function reads the sub-question id out of the prompt text and decides to crash,
  stall, or answer. Slightly grubby, entirely effective.
- **Policy-in-one-place worked.** Researcher failure, gap-analyst failure, and budget breach all
  route through `classify_error` + limitations; agents stay policy-free. The
  "one failed researcher never kills a run / a failed gap analyst just stops iteration" rules
  are each one small `except` block in the orchestrator.
- Structured-output validators doubled as **consistency enforcement across fields**
  (`saturated=true` ⟹ no follow-ups) — something JSON schema alone can't express.

**Design notes / limitations:**

- In the `quick` profile (max_waves=1) the gap analyst never runs — the loop exits before the
  gap stage. Intentional (quick = one cheap pass), but worth knowing when reading traces.
- Sub-question dedup is exact-match after normalization; a semantically-duplicate rephrasing
  will slip through and cost a researcher run. Accepted for v1.

---

## Entry 2 — 2026-07-18 — Phase 1: plan → research pipeline, and testing agents without keys

**Built:** the domain vocabulary (`models.py`), per-role usage ledger + budget (`deps.py`),
planner and researcher agents, single-wave orchestrator, plain-progress CLI, run.json writer,
and a 19-test offline suite.

**Learnings & dev-ex notes:**

- **`TestModel` refuses agents that carry server-side tools** — `UserError: TestModel does not
  support built-in tools`. The escape hatch is that `Agent.override()` accepts `native_tools=`,
  so tests strip them: `agent.override(model=TestModel(...), native_tools=[])`. Undocumented in
  any tutorial I saw; found by reading the `override` signature. This is THE pattern for
  offline-testing researcher-style agents.
- **`result.usage` is an attribute in pydantic-ai 2.13, not a method.** Older docs/examples say
  `result.usage()`. Caught it in the first smoke run; fixed the spike script.
- **Model-less agent singletons work beautifully.** `Agent(output_type=..., deps_type=...)`
  with no model + `agent.run(..., model=resolve_model(role, settings))` at the call site keeps
  routing entirely in config. `Agent.override(model=...)` still wins in tests, which is exactly
  the right precedence.
- **Dynamic instructions via `@agent.instructions` + `RunContext[Deps]`** make profile-aware
  prompts trivial (sub-question bounds, today's date, search budget all injected from deps).
- **`pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`** is a great belt-and-braces switch —
  any test that accidentally reaches for a real provider raises instead of spending money.
- Output validators + `ModelRetry` give a clean home for the "every claim needs a real quote
  and URL" rule — semantic validation lives next to the agent, policy stays in the orchestrator.

**Issues / decisions:**

- **Search costs can't be counted exactly offline.** Whether the SDK surfaces per-run
  web-search counts in `usage.details` is unverifiable without keys, so the ledger records a
  conservative upper bound (max_uses per researcher run) and `_reconcile_searches()` replaces
  it with real provider counters when present. Over-estimating cost is the safe direction for
  a budget guard. To re-check on first live run.
- `tests/` needed an `__init__.py` for cross-module fixture imports — pytest's rootdir munging
  strikes again.

---

## Entry 1 — 2026-07-18 — Phase 0: scaffold, API verification, and the keyless-sandbox reality

**Built:** repo scaffold with uv (`uv init --package`), full dependency set, config/telemetry
modules, the gateway spike script, this journal, and CLAUDE.md.

**Learnings & dev-ex notes:**

- **The docs were ahead of most tutorials, and the installed reality matched the docs.** Web
  research (against pydantic.dev, July 2026) warned that built-in tools moved from a
  `builtin_tools=` argument to `capabilities=[NativeTool(...)]`. Verified against installed
  `pydantic-ai-slim==2.13.0`: `from pydantic_ai.capabilities import NativeTool` works,
  `Agent(..., capabilities=...)` exists, and `WebSearchTool`/`WebFetchTool` expose exactly the
  documented params (`max_uses`, `allowed_domains`/`blocked_domains`, `enable_citations`,
  `max_content_tokens`). Lesson for anyone building on this stack: **verify the API surface
  against the installed package before writing code** — the ecosystem moves monthly and stale
  blog posts are the norm.
- **One-command environment.** `uv init --package` + two `uv add` lines gave a working src-layout
  package with locked deps in under a minute. Nothing to configure. This part of the Python
  story is now genuinely great.
- **`pydantic-ai-slim[anthropic,logfire]` is the right install** for an Anthropic-only project —
  the fat `pydantic-ai` package pulls every provider SDK you don't need.
- **LLMJudge trap confirmed in source**: `pydantic_evals`' `LLMJudge` defaults to an OpenAI judge
  model. With no OpenAI key in this project, every judge must set
  `model='gateway/anthropic:...'` explicitly or evals will hard-fail at runtime.

**Issues / limitations hit:**

- **The build sandbox has no API keys** (no `ANTHROPIC_API_KEY`, no `LOGFIRE_TOKEN`, no gateway
  key). Consequence: everything in v1 is developed against `TestModel`/`FunctionModel` offline;
  the two live questions — "do Anthropic server-side web tools work through the Gateway proxy?"
  and "what does a real run cost?" — are packaged as a ready-to-run spike
  (`scripts/spike_gateway_server_tools.py`) for the first keyed environment. The architecture
  hedges: a `routing = gateway | direct | split` setting isolates the answer to one env var.
- Sonnet 5's intro pricing ($2/$10 per MTok) expires 2026-08-31; the `PRICING` table in
  `config.py` carries a dated comment so cost estimates don't silently drift.

---

## Entry 0 — 2026-07-18 — Origin story: how this project started

This project began as a casual question in a Claude Code session — the user wanted a weekend
project to learn the modern Pydantic platform, and the idea sharpened over a few rounds of
pushback. The (verbatim, typos and all) prompts that shaped it:

1. The opening ask:
   > "If you were a dev that wants to test pydantic ai full stack ver the weekens
   > Which projwtc woils you create? Gibe me 3 ideas"

2. Scoping to the current Pydantic platform:
   > "It shouls covwr the lawst pydanrix stack primarly ai, logfire, evals and gateway"

3. Grounding in reality (this prompted a live fetch of pydantic.dev):
   > "Fetch pydanrix.dev and get the lawst contezt"

4. Raising the ambition past RAG demos:
   > "Somehtingmore agwntic? More interesring that qa rag"

5. The cost constraint era — initially Claude Max only, no extra services:
   > "I want somejting that onlynneeds my claude max account and pudantix token
   > No edtra services like aupabase ornotjer paid tools"

   (Research verdict: Pydantic AI can't bill to a Claude Max subscription and the Gateway needs
   a provider key, so the constraint was relaxed:)
   > "Ok we can ise anthopoc api key - what could we build"

6. The decisive pivot — a "Bug Hunter" agent benchmarked on seeded bugs was on the table, and
   the user killed it in favor of something real:
   > "Dont love it indont wamt to fake anyhtijg to test i
   > I wamt. Arela agent functinality
   > Like deep reseaexh agents ot something like that somehting useful incan open aource"

7. The commission, including this journal itself:
   > "Create a plan to create a sota deep reseaexh system on the pydantic stack
   > I also want you to keep a builder journal with all your learnjngs, insights,
   > Issues , kimitations, dev ex, etc..
   > Add to the jiirnal hownthis projwxt started, which wa sthen prompt"

**Decisions locked at planning time** (via explicit Q&A): CLI-first interface; the name
**`deepresearch`**; Pydantic AI Gateway wired in from day 1 (BYOK Anthropic key, spend caps).

**The concept:** a wave-based deep research pipeline — Planner → parallel Researchers using
Anthropic's server-side web search/fetch → Gap Analyst (iterate until saturated) → per-source
claim Verifier → two-stage Synthesizer — typed with Pydantic models at every boundary, traced
end-to-end in Logfire, measured with Pydantic Evals (including a verifier on/off A/B
experiment), and routed through the Gateway with hard spend caps. Constraints: one Anthropic
API key + one Pydantic (Logfire) account, no other services, local artifacts only.

The interesting bet being tested: **claim-level adversarial verification is the differentiator**
that most open-source deep-research clones skip — and the evals exist to prove (or refute) that
it's worth its cost.
