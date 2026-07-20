---
name: add-agent-role
description: Add a new pipeline agent role to deepresearch (a new stage with its own model choice and budget). Use when a change genuinely needs a new Role rather than riding an existing one.
---

# Adding a new agent role

**First ask: does this need a new Role at all?** The clarifier deliberately runs on the
planner's model and bills the planner ledger — no new Role, so profiles, RunRecord, and eval
tooling stayed untouched (journal 20). A new Role is only warranted when the stage needs its
own model selection knob and its own usage/budget line.

## The multi-file checklist (all of these, in order)

1. `src/deepresearch/models.py` — output type(s) for the new agent.
2. `src/deepresearch/agents/<role>.py` — agent definition + instructions + output validator
   only. No model bound, no control flow; the orchestrator passes `model=`.
3. `src/deepresearch/config.py`:
   - add the role to the `Role` literal;
   - add a model for it to `RoleModels` and to **all three** `PROFILES` entries;
   - extend `role_model_settings` if it needs caching/thinking treatment.
4. `src/deepresearch/orchestrator.py`:
   - add the role to the `ROLES` tuple (RunRecord.models_used derives from it);
   - a `UsageLimits` constant (`<ROLE>_LIMITS`);
   - the call site via `_run_agent(..., role="<role>", usage_limits=<ROLE>_LIMITS)` —
     sequencing, degradation policy (`classify_error`), and budget checks live here.
5. `.env.example` — the `DEEPRESEARCH_MODELS__<ROLE>=` override line.
6. Tests: scripted TestModel/FunctionModel coverage for the new stage, including its
   degradation path; update `tests/test_behavior_evals.py` if the pipeline shape changed.
7. Evals: extend `evals/` only if the new stage changes report quality semantics.
8. JOURNAL.md entry: why the role exists, model choice reasoning, cost impact.

## Sanity checks

- `uv run pytest` green, `uv run ruff check .` clean.
- `record.models_used` in a scripted run contains the new role.
- Budget still binds: the new stage's spend shows up in `record.usage` and cost estimates.
