"""Run-scoped dependencies injected into agents, plus usage/cost accounting.

One ``RunUsage`` per role (not one global): each role runs exactly one model, so per-role
ledgers let ``Budget.estimate`` price tokens correctly while still summing to run totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.usage import RunUsage

from .config import PRICING, SEARCH_COST_USD, Role, Settings
from .models import RoleUsage


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

    def usage_for(self, role: Role) -> RunUsage:
        """The accumulator to pass as ``usage=`` into every run for this role."""
        return self.by_role.setdefault(role, RunUsage())

    def record_searches(self, n: int) -> None:
        self.searches += n

    def snapshot(self, settings: Settings) -> dict[str, RoleUsage]:
        return {
            role: RoleUsage(
                model=settings.bare_model(role),  # type: ignore[arg-type]
                requests=u.requests,
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
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
            bare = self.settings.bare_model(role)  # type: ignore[arg-type]
            in_rate, out_rate = PRICING.get(bare, (0.0, 0.0))
            total += (usage.input_tokens or 0) / 1_000_000 * in_rate
            total += (usage.output_tokens or 0) / 1_000_000 * out_rate
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
