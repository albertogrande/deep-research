# Builder Journal — deepresearch

A running log of learnings, insights, issues, limitations, and developer-experience notes
from building a deep research agent system on the full Pydantic stack (Pydantic AI, Logfire,
Pydantic Evals, Pydantic AI Gateway). Newest entries at the top; entry 0 (the origin story)
stays at the bottom.

Convention: every working session appends an entry before pushing — what was built, what was
learned, what broke, what the stack made easy or hard, and what it cost.

---

## Entry 8 — 2026-07-19 — README polish

Rewrote the README as the project's front door after researching current best practice
(makeareadme.com, banesullivan/README, jehna/readme-best-practices). Applied the load-bearing
principles: lead with one line of *what + why*; a **Highlights** list of selling points up top;
show it *in action* (architecture diagram + real output shape); keep it scannable with headers;
push long reference material (full flag table, limitations) into collapsible `<details>` so the
main scroll stays crisp; state requirements and license plainly. Verified every asserted fact
against the code first (47 offline tests, the exact flag set, exit codes, depth/cost table) so
nothing in the README drifts from reality — the one exception is the sample summary block, whose
numbers are illustrative of the *output format*, and the A/B results, which stay a placeholder
until the first live run. No code changed.

## Entry 7 — 2026-07-18 — Phase 6: evals, and the v1 retrospective

**Built:** the 8-case eval dataset (YAML, round-tripped through `Dataset.to_file`/`from_file`),
five custom objective evaluators + three LLM judges, the verifier on/off A/B experiment script,
evaluator unit tests (httpx MockTransport for URL resolution), gated live smoke tests, README,
LICENSE. Suite: 47 offline tests, 0 API calls, ~1.5s.

**Learnings & dev-ex notes (Pydantic Evals):**

- **Custom evaluators are just dataclasses returning dicts** — `{"citation_coverage": 0.83}` —
  and can be sync or async. Friction was near zero; the whole objective suite was an hour of
  work. Returning `{}` for "not applicable" (e.g. verified-claim-rate on a no-verify run) is a
  clean convention.
- **The LLMJudge OpenAI-default trap is real**: `LLMJudge(...)` with no `model=` would crash
  every eval run in this OpenAI-keyless project — at runtime, not import time. Every judge here
  sets `model=` explicitly, and `judge_model()` respects the routing setting. Also note the
  `score`/`assertion` config is a TypedDict (`OutputConfig`) — `score={"evaluation_name":
  "faithfulness"}, assertion=False` gives a named 0–1 score instead of a pass/fail.
- **Designing the task's return type around evaluators pays off.** The eval task returns
  `EvalOutput{report_markdown, record: RunRecord}` — the audit trail we already write. No
  span-mining, no scraping stdout: evaluators read typed data. The run.json investment from
  Phase 1 became the eval substrate for free.
- `Dataset(...)` requires `name=` in this version — minor, but another docs-drift catch.
- Evaluator ideas that earn their place: **unsupported-leakage** (assert excluded claims
  really are absent from the report) and **unverifiable-rate** (measures how much of the web
  the verifier can actually reach — the paywall reality check).

**Retrospective — what the weekend proved about the stack:**

1. **Pydantic AI** is genuinely productive: model-less agent singletons + per-run `model=`,
   output validators with ModelRetry, and `Agent.override` made a 5-agent pipeline fully
   offline-testable. Wishlist: subscription (Claude Max) billing support; a documented way to
   pass per-run context to output validators; TestModel support for built-in tools.
2. **The architecture decisions that mattered most**: policy-in-orchestrator (agents stay
   dumb), code-owned citations, deterministic budget, degradation-over-death everywhere.
3. **Still unproven without keys** (the honest list): server-tools-through-gateway (spike
   ready), real citation quality from Haiku researchers, the true unverifiable rate, real
   costs vs. the estimator, Logfire trace ergonomics at depth, and the headline question —
   does the verifier measurably improve faithfulness? Every one of these has a script or eval
   waiting; first keyed session should run: spike → one standard run → `-m evals.run_evals` →
   `-m evals.experiments.verifier_ab`, then paste numbers into README and journal the verdict.

**v1 cuts, accepted knowingly:** no resume/checkpointing; exact-match dedup; sequential
sections; stage-level streaming; env-only per-role model overrides; cache-blind cost model;
online evals not wired (pydantic_evals.online exists — natural v2, feeding production traces
back into the dataset).

---

## Entry 6 — 2026-07-18 — Phase 5: the product surface — live UI and an exit-code contract

**Built:** Rich Live progress renderer (header panel, per-wave researcher trees, verification
pass-rate line, synthesis status, cost ticker), plain/quiet/json output modes, and a tested
exit-code contract: 0 ok · 1 fatal · 2 synthesis-fallback · 3 spend-refusal · 130 interrupted.

**Learnings & dev-ex notes:**

- **The UI-agnostic event callback earned its keep.** The orchestrator emits frozen dataclass
  events; the Live renderer, the plain printer, and (later) eval harnesses are all just
  different consumers. Zero orchestrator changes were needed to add the live UI.
- Python 3.10+ `match`/`case` on frozen event dataclasses is a genuinely pleasant way to write
  both renderers — pattern-matching with field capture reads like a spec.
- **Exit codes are product API.** `--json` + documented exit codes make the CLI scriptable
  (cron a research run, alert on exit 3 = spend cap). Locked with typer's CliRunner tests —
  including the 402-spend-refusal path, scripted via a FunctionModel that raises
  `ModelHTTPError(402)`.
- Rich's `Live` needs care with agent SDKs that also print (console logging off in telemetry
  was the right call in Phase 0 — no fighting over the terminal).

**Still pending keys:** everything to date is offline-verified. The first live run will exercise
streamed server-tool behavior, real Logfire traces, and true costs — journal entry to follow.

---

## Entry 5 — 2026-07-18 — Phase 4: synthesis — models write prose, code owns the numbers

**Built:** two-stage synthesizer (outline agent → per-section agent), code-built citation map,
report assembly, claims-dump fallback artifact, golden-file rendering test; full pipeline now
runs end-to-end offline (35 tests).

**Learnings & dev-ex notes:**

- **The "models write prose, code owns structure" split is the best decision in the codebase.**
  Citation numbers are assigned by `build_citation_map` and injected into section prompts as
  fixed `[n]` markers; the References block, Limitations section, TL;DR quote, and headings are
  all assembled by code. Numbering physically cannot drift, and the golden-file test locks the
  format byte-for-byte.
- **Passing per-run data to output validators required a small hack worth knowing:** validators
  see only `ctx.deps`, so the orchestrator stashes `outline_valid_claim_ids` /
  `outline_required_claim_ids` on the (mutable, run-scoped) Deps dataclass right before the
  outline run. Works cleanly because Deps is per-run, but it's implicit coupling — an
  `Agent.run(context=...)` kwarg would be nicer. Dev-ex wishlist item for pydantic-ai.
- The outline validator's "≤30% of supported claims may go unassigned" rule is the mirror image
  of hallucination control: it stops the model from *dropping* evidence, while the unknown-id
  check stops it from *inventing* evidence.
- **Failure economics:** synthesis failure downgrades to a claims-dump artifact (still cited,
  still grouped) instead of losing the run — research money is never thrown away. Spend-cap
  aborts write the same fallback before re-raising.

**Limitation accepted:** sections are written sequentially (simpler citation bookkeeping,
better Logfire readability); parallel section writing is a v2 optimization.

---

## Entry 4 — 2026-07-18 — Phase 3: the verifier — trust nothing, degrade everything

**Built:** per-source verifier agent (WebFetchTool, `max_uses=2`), `group_claims_by_url`
(canonical grouping, original URL preserved for fetchability), the verification stage with
verdict merge, and 5 verifier tests.

**Learnings & dev-ex notes:**

- **The in-context-URL constraint shapes the design.** Anthropic's web fetch only fetches URLs
  already present in context, so the verifier prompt *embeds* the source URL — that's not
  cosmetic, it's what makes the fetch legal. Any redesign that moves URLs out of the prompt
  silently breaks fetching.
- **Three defensive layers on verdicts, each one catching a distinct model failure mode:**
  (1) output validator: `fetch_ok=false ⇒ all unverifiable` (self-consistency, enforced with
  ModelRetry); (2) merge filter: verdicts for hallucinated or cross-source claim ids are
  dropped; (3) code backfill: any claim the model skipped becomes `unverifiable` — a skipped
  claim must never pass as verified. Tests script each layer separately with FunctionModel.
- The **`unverifiable ≠ unsupported` distinction** costs ~10 lines and buys honest reports:
  contradicted claims will be excluded; merely-unfetchable ones survive with a marker. (Report
  rendering lands in Phase 4.)
- Budget-aware degradation reads nicely: verification silently skips itself when <25% of budget
  remains, recording a limitation — the report (Phase 4's payoff stage) keeps its funding.

**Watch-item for the first live run:** how often real-world sources 403 the fetcher. If the
unverifiable rate is high (>40%), the verifier's value proposition weakens and that becomes a
headline finding for the evals.

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
