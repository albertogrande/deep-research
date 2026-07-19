"""Regression: UsageLimits must apply per run, not against the shared role accumulator.

With the pre-fix shared `RunUsage`, the second sibling run would already see the first run's
request count and trip `request_limit` (e.g. multi-section synthesis spuriously falling back
to a claims dump). Each run now gets its own `RunUsage`, merged into the ledger afterwards.
"""

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.test import TestModel

from deepresearch.config import Settings
from deepresearch.deps import Budget, Deps, UsageLedger
from deepresearch.orchestrator import _run_agent


def _deps(tmp_path) -> Deps:
    settings = Settings(_env_file=None, profile="quick", output_dir=str(tmp_path))
    return Deps(
        settings=settings,
        budget=Budget(max_cost_usd=1.0, settings=settings),
        ledger=UsageLedger(),
        run_id="r",
        today="2026-07-19",
    )


async def test_usage_limits_are_per_run_and_ledger_accumulates(tmp_path):
    deps = _deps(tmp_path)
    agent = Agent(output_type=str)

    # request_limit=1 with a TestModel that makes exactly one request per run. A shared
    # accumulator would trip on the 2nd/3rd run; per-run usage does not.
    with agent.override(model=TestModel(custom_output_text="ok")):
        for _ in range(3):
            result = await _run_agent(
                agent, "hi", role="planner", deps=deps, usage_limits=UsageLimits(request_limit=1)
            )
            assert result.output == "ok"

    # ...and the ledger still accumulates across runs for cost accounting.
    assert deps.ledger.by_role["planner"].requests == 3


def test_snapshot_carries_cache_tokens(tmp_path):
    from pydantic_ai.usage import RunUsage

    deps = _deps(tmp_path)
    deps.ledger.record(
        "researcher",
        RunUsage(input_tokens=1000, output_tokens=200, cache_read_tokens=800, cache_write_tokens=150),
    )
    snap = deps.ledger.snapshot(deps.settings)
    assert snap["researcher"].cache_read_tokens == 800
    assert snap["researcher"].cache_write_tokens == 150
