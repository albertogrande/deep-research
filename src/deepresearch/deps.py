"""Run-scoped dependencies injected into agents, plus usage/cost accounting.

One ``RunUsage`` per role (not one global): each role runs exactly one model, so per-role
ledgers let ``Budget.estimate`` price tokens correctly while still summing to run totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from genai_prices import Usage as PriceUsage
from genai_prices import calc_price
from pydantic_ai.usage import RunUsage

from .config import PRICING, SEARCH_COST_USD, Role, Settings
from .models import RoleUsage


def price_role_usage(bare_model: str, usage: RunUsage) -> float:
    """USD token cost for one role's accumulated usage.

    Uses ``genai-prices`` (bundled offline snapshot; knows Anthropic model ids, the Sonnet 5
    intro→standard date transition, and cache-token rates). Falls back to the static PRICING
    table if a model is unknown to the snapshot.
    """
    try:
        calc = calc_price(
            PriceUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_tokens or 0,
                cache_write_tokens=usage.cache_write_tokens or 0,
            ),
            model_ref=bare_model,
            provider_id="anthropic",
        )
        return float(calc.total_price)
    except Exception:
        in_rate, out_rate = PRICING.get(bare_model, (0.0, 0.0))
        return (usage.input_tokens or 0) / 1_000_000 * in_rate + (
            usage.output_tokens or 0
        ) / 1_000_000 * out_rate


class BudgetExceeded(Exception):
    """Raised at a budget checkpoint when the estimated cost crosses the cap."""

    def __init__(self, stage: str, estimate: float, cap: float):
        self.stage = stage
        self.estimate = estimate
        self.cap = cap
        super().__init__(f"budget cap ${cap:.2f} exceeded at {stage} (estimate ${estimate:.2f})")


@dataclass
class UsageLedger:
    """Per-role RunUsage accumulators plus a server-side web-search counter."""

    by_role: dict[str, RunUsage] = field(default_factory=dict)
    searches: int = 0

    def record(self, role: Role, usage: RunUsage) -> None:
        """Merge one completed run's usage into the role accumulator.

        Each ``agent.run`` gets its OWN ``RunUsage`` (so ``UsageLimits`` is enforced per run,
        not against the shared role total), then merges here for cost accounting and cost
        estimation. ``incr`` also merges the ``details`` dict, preserving web-search counters.
        """
        self.by_role.setdefault(role, RunUsage()).incr(usage)

    def record_searches(self, n: int) -> None:
        self.searches += n

    def snapshot(self, settings: Settings) -> dict[str, RoleUsage]:
        return {
            role: RoleUsage(
                model=settings.bare_model(role),  # type: ignore[arg-type]
                requests=u.requests,
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=u.cache_read_tokens or 0,
                cache_write_tokens=u.cache_write_tokens or 0,
            )
            for role, u in self.by_role.items()
        }


@dataclass
class Budget:
    """Deterministic cost estimation and enforcement against the run's USD cap."""

    max_cost_usd: float
    settings: Settings

    def estimate(self, ledger: UsageLedger) -> float:
        total = ledger.searches * SEARCH_COST_USD
        for role, usage in ledger.by_role.items():
            total += price_role_usage(self.settings.bare_model(role), usage)  # type: ignore[arg-type]
        return total

    def remaining_fraction(self, ledger: UsageLedger) -> float:
        if self.max_cost_usd <= 0:
            return 0.0
        return max(0.0, 1.0 - self.estimate(ledger) / self.max_cost_usd)

    def checkpoint(self, ledger: UsageLedger, stage: str) -> None:
        estimate = self.estimate(ledger)
        if estimate >= self.max_cost_usd:
            raise BudgetExceeded(stage, estimate, self.max_cost_usd)


@dataclass
class Deps:
    """Dependencies available to every agent via RunContext."""

    settings: Settings
    budget: Budget
    ledger: UsageLedger
    run_id: str
    today: str  # ISO date; injected into prompts and stamped on claims

    # Set by the orchestrator just before the outline run; consumed by its output validator.
    outline_valid_claim_ids: frozenset[str] = frozenset()
    outline_required_claim_ids: frozenset[str] = frozenset()
