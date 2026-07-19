# Builder Journal — deepresearch

A running log of learnings, insights, issues, limitations, and developer-experience notes
from building a deep research agent system on the full Pydantic stack (Pydantic AI, Logfire,
Pydantic Evals, Pydantic AI Gateway). Newest entries at the top; entry 0 (the origin story)
stays at the bottom.

Convention: every working session appends an entry before pushing — what was built, what was
learned, what broke, what the stack made easy or hard, and what it cost.

---

## Entry 16 — 2026-07-19 — Context hygiene: semantic dedup, claim ranking, per-domain caps

Phase 2 of the SOTA effort — the Jina node-DeepResearch recipe (embedding dedup + URL ranking)
adapted to this repo's constraints. The constraint that shaped everything: **Anthropic-only
means no embedding endpoint**, and a Haiku-judge dedup would add per-wave spend and latency
for marginal gain at this scale (dozens of claims, not thousands). So dedup is embedding-free
and deterministic: `similarity()` = max of character-level `difflib.SequenceMatcher` ratio
(catches small insertions — "capital" vs "capital city") and token-set Jaccard (catches
reorders — "France's capital is Paris" vs "Paris is France's capital"). Runs offline, costs
nothing, fully unit-testable.

Decisions worth recording:
- **Thresholds are conservative by design** (0.85 questions / 0.9 claims on pre-normalized
  text). A false-positive dedup silently drops a genuinely new question — strictly worse than
  letting the occasional paraphrase through. Measured on fixture pairs in tests: true
  paraphrases score 0.87–1.0, genuinely different questions 0.17–0.68 — comfortable margin.
- **Cross-source near-duplicates are kept, not folded.** A second source saying the same thing
  is distinct citation value. Instead, both sides' `corroborations` increment — the counter is
  now a real cross-source signal (it previously only counted exact same-source resightings),
  which feeds directly into ranking and, next phase, the gap analyst's stopping rubric.
- **Ranking and caps shape what agents SEE, never what the run records.** `rank_claims`
  (confidence tier → corroborations → earliest wave) + `cap_per_domain(3)` apply only when a
  digest listing exceeds its cap; `RunRecord` and the report keep everything.
- **One deliberate deviation from the phase plan**: the plan said to cap `outline_digest` too.
  Reading the outline validator killed that idea — it *requires* ≥70% of supported claims
  assigned to sections, and the outline agent can only assign ids it saw. Capping there would
  either break the retry loop or silently drop claims from the report (invariant violation).
  So `outline_digest` is rank-ordered (strongest evidence first) but never truncated.

One existing test had to change semantics: the Phase-1 listing-cap test used 11 same-host
claims, which the new domain cap squeezes to 3 — updated to distinct hosts so each mechanism
is tested in isolation. 66 offline tests green. Cost: **$0** (offline only).

## Entry 15 — 2026-07-19 — Per-branch compression + evolving central digest (IterResearch-style)

First phase of the "SOTA-comparable" effort planned this session (research: Tongyi's IterResearch
per-round workspace reconstruction is the best-measured scaffold technique, +14.5pp in their
ablation; LangChain's open_deep_research ships per-branch compression too). The shape adapted
to this codebase:

- **Compression rides the researcher's existing structured output.** `Findings` gained a
  `summary` field (2–4 sentences: what was established, what wasn't). The researcher writes it
  in the same call that produces the claims — zero extra requests, zero extra cost. No separate
  compressor agent.
- **`central_digest()` is the evolving workspace** — and "evolving" here means *rebuilt from
  typed state every round*, never appended to. Per sub-question: the researcher's own summary,
  claim/source counts, and a capped claim listing (8/sq; hidden claims are counted, never lost —
  caps shape what agents see, not what the run records). A code-computed **NEW THIS WAVE**
  section (from `claim.wave == current_wave`) plus inline `[NEW]` markers let the gap analyst
  judge the marginal value of the last wave at a glance — groundwork for evidence-aware stopping
  in a later phase.
- **`known_so_far_brief()`** injects an ALREADY ESTABLISHED block into wave ≥2 researcher
  prompts, built from the wave-1 summaries. Hard-capped at 2400 chars (~600 tokens, asserted in
  tests by character count) with squeeze-then-drop-oldest degradation. Wave-1 researchers stay
  fully isolated — parallelism and independence are the point of the fan-out; context awareness
  only pays once there is context.
- `gap_digest()` now delegates its body to `central_digest()` and keeps only the
  ALREADY-ASKED-QUESTIONS tail. Orchestrator plumbing: `_RunState.summaries_by_sq`, ingested
  next to `notes_by_sq`.

Dev-ex notes: the FunctionModel prompt-inspection pattern (grab the prompt text out of
`messages`, key it by `sq-NN`) keeps proving itself — the new test asserts wave-2 prompts
contain the brief and wave-1 prompts don't, fully offline. Because `summary` has a default,
every existing TestModel fixture kept working untouched; only `FINDINGS_ARGS` gained the field
so the wave test could assert the summary text lands in the wave-2 prompt. 59 offline tests
green (was 54). Also fixed a pre-existing `ruff format` drift in `cli.py` that would have
failed CI's format check on the next PR.

Accepted limitation: the researcher *writes* its summary but nothing enforces quality — a lazy
model can emit one sentence. Fine: the digest falls back to claim counts when the summary is
empty, and the claim listing is the ground truth anyway. Cost of this entry: **$0** (offline
only, by explicit user decision for this whole effort).

## Entry 14 — 2026-07-19 — README refreshed to current state; promoted to main

Doc-only follow-up to entry 13. Brought the README in line with the actual repo: test badge and
Development section now say **54 offline** (was 53); the critic-gate highlight and the flags table
document the new `--max-revise` flag (default 2, `0` disables the loop); added a **minimum-cost run**
line to Usage (`--depth quick --no-verify --max-revise 0` ≈ $0.24, with the note that the 9-search
`$0.09` floor is the irreducible part and the revise loop is the biggest saveable lever). No code
change. This session's work was promoted to `main` at the user's explicit request.

## Entry 13 — 2026-07-19 — First real cloud run + a min-cost lever (configurable revise loop)

The whole point of a session: the project **finally ran end-to-end in the cloud environment**.
The user had added `PYDANTIC_AI_GATEWAY_API_KEY` and `LOGFIRE_TOKEN` to the new environment;
no `ANTHROPIC_API_KEY` (so routing stayed `gateway`, the default, which is exactly what those
two tokens support). `uv sync` clean, 53 offline tests green.

**Gateway spike re-run, new environment → still green.** Per CLAUDE.md the server-tools spike
must run once per new environment. `gateway/anthropic:claude-haiku-4-5` + `WebSearchTool` answered
"1991" through the Gateway; Logfire project URL printed (`algrande/starter-project`), so traces
land. Verdict unchanged: keep `routing=gateway`. Direct leg skipped (no direct key) — fine.

**Two live runs, both cheap.** Goal was "spend the minimum possible", so both used `--depth quick`,
all six roles forced to Haiku via `DEEPRESEARCH_MODELS__*`, and `--no-verify`:
- Run 1 (green-tea benefits, default revise loop): **$0.42**, 194s. Produced a genuinely good,
  densely-cited report (46 claims, 9 searches). But it tripped the $0.40 cap — the critic **revise
  loop was ~2/3 of the cost**: it rewrote all 6 sections twice (2 revise passes) and, with Haiku
  as critic, *never* returned `ship` — it graded `revise` on every pass, so the loop always ran to
  the cap. The budget checkpoint fires *between* passes, so a run can end a few cents over the cap.
- Run 2 (first modern Olympics, `--max-revise 0`): **$0.24**, 68s. Critic still grades once (useful
  signal recorded in run.json) but no rewrite happens. That knocked ~$0.15 off and ~2/3 off wall time.

**The lever I added.** `MAX_REVISE_ITERS` was a hardcoded module constant in `orchestrator.py`.
Promoted it to a real setting: `Settings.max_revise_iters` (default 2, behavior unchanged),
threaded through `deps.settings`, with a `--max-revise N` CLI flag and a `.env.example` line.
`0` disables the revise loop entirely — the cheapest sane config. Kept the architecture rule
intact (orchestrator still *owns* the loop policy; it just reads the bound from settings now).
Two test touch-ups: the capped-at-max-iters test now reads `quick_settings.max_revise_iters`
instead of importing the deleted constant, plus a new test that `max_revise_iters=0` grades once
and writes the draft with zero rewrites (54 offline tests now).

**Cost insight worth remembering:** at the quick tier the **search cost is the floor** — 3
researchers × 3 searches = 9 × $0.01 = **$0.09 fixed**, before a single token. Everything else
(Haiku planning/research/synthesis) is small change. So the cheapest useful run is roughly
`$0.09 searches + ~$0.10–0.15 Haiku tokens ≈ $0.20–0.25`. To go lower you'd cut searches
(needs a profile/field change, not just model overrides) — noted, not done.

**Cheapest-run recipe (recorded for future me):**
`--depth quick --no-verify --max-revise 0 --max-cost 0.25` + all roles Haiku via
`DEEPRESEARCH_MODELS__*`. ~$0.24, ~70s, still a real cited report.

**Dev-ex:** the Gateway "just worked" with only its own key — no Anthropic key juggling — and
Logfire lit up with zero extra config beyond the token. The one rough edge is the Haiku-as-critic
never-ships behavior: fine for cost experiments, but it means the critic loop only adds value with
a stronger critic model (the standard/deep profiles use Sonnet/Opus for `critic`, which is right).

## Entry 12 — 2026-07-19 — Repo renamed pydantic → deep-research

The user renamed the GitHub repo `albertogrande/pydantic` → `albertogrande/deep-research` (the
package was always `deepresearch`; the repo name now matches). GitHub surfaced it as a "repository
moved" redirect on push — harmless, pushes still landed correctly, verified by SHA. Repointed the
local git remote to the new path (proxy accepts it; `ls-remote` confirmed) and updated the one
tracked reference to the old name (the README CI badge). Local working directory is still
`/home/user/pydantic` — cosmetic only, left as-is.

## Entry 11 — 2026-07-19 — Borrowed a critic gate + revise loop from a sibling deep-research repo

The user pointed me at their *other* deep-research project — `albertogrande/deep-research`, a
TypeScript/Node full-stack system (Next.js reader + a worker that shells out to the `claude` CLI
on a Max plan, five-phase pipeline, weekly cron, Kindle delivery). Added it to the session,
shallow-cloned it, and mined it for what our Python system lacks. Nothing copies across languages;
the value was in **design and prompts**.

**The borrow that mattered: a Critic gate + bounded revise loop.** Their pipeline is
Planner → Searchers → Synthesizer → **Critic → revise (≤2)**. The planner emits explicit
*acceptance criteria*; after synthesis a critic grades the draft against them and returns
ship|revise + guidance; the synthesizer re-runs with that guidance. We had the raw material
already — `ResearchPlan.done_criteria` existed but was barely used — so this slotted in cleanly:

- New `critic` agent (`agents/critic.py`) + `Critique` model. Prompt ported from their critic:
  a six-point checklist (acceptance-criteria coverage, grounding, non-redundancy, structure fit,
  date/tone discipline, contradiction handling) and — the part I most wanted — an explicit
  "**never critique on length; if it's long because of repetition, the issue is repetition**".
- Revise loop in `_synthesis_stage`: grade → on `revise`, rewrite the sections with the critic's
  guidance appended, up to `MAX_REVISE_ITERS`. **Key adaptation to our architecture:** the
  outline and citation map stay FIXED across revises — only section prose is regenerated — so our
  "code owns citation numbers" invariant survives the loop (their system lets the model rewrite
  the whole doc; we can't, or numbering would drift).
- Hardened the section-writer prompt with the same discipline (absolute dates, no
  meta-commentary/sign-off, repetition-is-the-worst-sin) so writer and critic agree on the rules.
- Degradation preserved: a flaky critic ships the un-critiqued draft (never sinks a good report);
  the revise loop is budget-checkpointed so it can't blow the cap; capping at max-iters ships the
  last draft with verdict `revise` recorded. New `critic` role added across profiles (mirrors the
  synthesizer tier). Recorded `critique_verdict`/`critique_iterations`/`critique_issues` in
  run.json and the CLI summary.

**Dev-ex note:** their prompts are unusually well-engineered — the "length is determined by the
material, not policed" philosophy is a genuinely good idea I'd have under-weighted, and it's now
in both our synthesizer and critic. Two new revise-loop tests (revise-once-then-ship;
capped-at-max-iters); suite at 53 offline. Left their repo untouched.

## Entry 10 — 2026-07-19 — Borrowed from a sibling project (semantica-ai)

A separate planning-only branch (`semantica-ai`: an n8n→Pydantic AI RAG migration plan) was
about to be deleted. Different domain, same stack — so I mined it for cross-cutting patterns
worth keeping, and applied four:

1. **`genai-prices` for cost estimation** (their plan used it for pricing). It turns out to be a
   *transitive dependency of pydantic-ai-slim already*, ships a bundled offline snapshot, and
   knows every model we use — including the Sonnet 5 intro→standard **date transition** and
   **cache-token** rates. This retires two limitations I'd journaled (stale hand-maintained
   pricing; cache-blind cost) in one move. `deps.price_role_usage` calls it and falls back to
   the static `PRICING` table only for unknown models. Dev-ex: nice to discover a first-party
   Pydantic-ecosystem lib solving exactly the thing I'd hand-rolled.
2. **CI** (`.github/workflows/ci.yml`): uv sync + ruff + format check + offline pytest on push
   and PRs. We had 48 tests and no gate running them; CLAUDE.md already assumed CI existed.
3. **`execution_id = trace id` pattern** → `RunRecord.logfire_trace_id`. Each run.json (and the
   CLI summary) now carries the hex trace id, so you can jump straight from an artifact to its
   Logfire trace. `telemetry.current_trace_id()` reads it from the active OTel span (None when
   keyless).
4. **`CitationIntegrity`** evaluator (their deterministic citation check): every `[n]` in the
   body resolves to a listed, contiguously-numbered reference — a strong non-flaky complement to
   the existing coverage/faithfulness evaluators.

Skipped their broader `instrument_httpx/asyncpg/openai` (we have no DB, and Anthropic calls are
already traced by `instrument_pydantic_ai` — HTTP-level spans would just double the noise).
Suite: 51 offline tests green. The migration docs themselves are domain-specific to their RAG
app and weren't copied; only the stack-level techniques were.

## Entry 9 — 2026-07-19 — Code review: a real UsageLimits bug, and the fix

Ran an xhigh code review over the whole codebase. It surfaced a genuine correctness bug worth
recording as a stack learning, plus five smaller fixes.

**The bug (would have bitten on the first real run):** I passed one shared per-role `RunUsage`
accumulator into every `agent.run(usage=…)` so cost accounting could sum per role. But
pydantic-ai enforces `usage_limits` against *that same object* — I read the installed source to
confirm: `run()` does `usage = usage or RunUsage()`, seeds `ctx.state.usage` with it, and
`check_before_request` tests `ctx.state.usage.requests >= request_limit`. So a **shared**
accumulator makes `UsageLimits` cumulative across sibling runs, not per-run. Consequence: the
synthesizer's outline + section runs share one accumulator, so `SECTION_LIMITS(request_limit=4)`
trips `UsageLimitExceeded` after ~2 sections → **every report with 3+ sections would have
spuriously fallen back to the claims dump.** Same mechanism made `RESEARCHER_LIMITS` a wave-wide
budget. Proven with a throwaway repro (run 1 ok, runs 2–3 tripped) and now guarded by
`tests/test_usage_accounting.py`.

**Fix:** each `agent.run` gets its own fresh `RunUsage` (correct per-run limits), merged into
the role ledger afterwards via `RunUsage.incr` (which also merges the `details` dict, so the
web-search counters `_reconcile_searches` reads survive). Wrapped in one `_run_agent` helper,
which also removed the `model=`/`usage=`/`resolve_model` boilerplate duplicated at six call
sites.

**Stack lesson:** when sharing a `RunUsage` for accounting, remember it is *also* the limit
enforcement surface — share it only across a genuine parent/child delegation tree (where you
want tree-wide limits), never across independent sibling runs. The pydantic-ai docs frame
`usage=` as an accumulator; the limit-coupling is easy to miss.

**Also fixed:** wave-loop budget-fatal path discarded successful researchers' claims (now
ingests all successes before raising); centralized the routing prefix in
`config.provider_prefix` so the eval judge can't drift from `resolve_model`; removed a dead
variable; corrected the CLAUDE.md "pure functions" wording to name the `write_*` I/O boundary.
Two low-impact items (an O(n²) dedup rebuild; verifier siblings not cancelled on abort) were
judged not worth the added complexity in v1. Suite: 48 offline tests green.

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
