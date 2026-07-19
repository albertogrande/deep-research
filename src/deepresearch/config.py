"""Settings, depth profiles, model pricing, and model-string resolution.

Model strings (including the ``gateway/`` vs ``anthropic:`` prefix) are spelled in
:func:`resolve_model` and nowhere else in the codebase.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["planner", "researcher", "gap_analyst", "verifier", "synthesizer"]
Routing = Literal["gateway", "direct", "split"]

# $/MTok (input, output). Sonnet 5 is intro pricing through 2026-08-31; $3/$15 after.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
}

# Anthropic server-side web search: $10 per 1,000 searches. Web fetch is free.
SEARCH_COST_USD = 0.01

# Roles whose agents carry Anthropic *server-side* tools (web search/fetch). Under
# routing="split" these go direct to Anthropic while everything else uses the gateway —
# the fallback if server tools turn out not to pass through the gateway proxy.
SERVER_TOOL_ROLES: frozenset[str] = frozenset({"researcher", "verifier"})


class RoleModels(BaseModel):
    """Bare model names (no provider prefix) per pipeline role."""

    planner: str
    researcher: str
    gap_analyst: str
    verifier: str
    synthesizer: str


class Profile(BaseModel):
    max_waves: int
    min_sub_questions: int
    max_sub_questions: int
    searches_per_researcher: int
    concurrency: int
    models: RoleModels
    default_max_cost: float


PROFILES: dict[str, Profile] = {
    "quick": Profile(
        max_waves=1,
        min_sub_questions=2,
        max_sub_questions=3,
        searches_per_researcher=3,
        concurrency=3,
        models=RoleModels(
            planner="claude-haiku-4-5",
            researcher="claude-haiku-4-5",
            gap_analyst="claude-sonnet-4-6",
            verifier="claude-haiku-4-5",
            synthesizer="claude-sonnet-4-6",
        ),
        default_max_cost=0.50,
    ),
    "standard": Profile(
        max_waves=2,
        min_sub_questions=3,
        max_sub_questions=5,
        searches_per_researcher=5,
        concurrency=4,
        models=RoleModels(
            planner="claude-sonnet-4-6",
            researcher="claude-haiku-4-5",
            gap_analyst="claude-sonnet-4-6",
            verifier="claude-haiku-4-5",
            synthesizer="claude-sonnet-5",
        ),
        default_max_cost=2.00,
    ),
    "deep": Profile(
        max_waves=4,
        min_sub_questions=4,
        max_sub_questions=7,
        searches_per_researcher=6,
        concurrency=5,
        models=RoleModels(
            planner="claude-sonnet-5",
            researcher="claude-haiku-4-5",
            gap_analyst="claude-sonnet-5",
            verifier="claude-haiku-4-5",
            synthesizer="claude-opus-4-8",
        ),
        default_max_cost=8.00,
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEEPRESEARCH_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: str = "standard"
    routing: Routing = "gateway"
    models: RoleModels | None = None  # per-role overrides, e.g. DEEPRESEARCH_MODELS__PLANNER=...
    max_cost: float | None = None  # USD; None -> profile default
    verify: bool = True
    concurrency: int | None = None  # None -> profile default
    output_dir: str = "runs"

    @property
    def prof(self) -> Profile:
        try:
            return PROFILES[self.profile]
        except KeyError:
            raise ValueError(
                f"unknown profile {self.profile!r}; expected one of {sorted(PROFILES)}"
            ) from None

    @property
    def effective_max_cost(self) -> float:
        return self.max_cost if self.max_cost is not None else self.prof.default_max_cost

    @property
    def effective_concurrency(self) -> int:
        return self.concurrency if self.concurrency is not None else self.prof.concurrency

    def bare_model(self, role: Role) -> str:
        models = self.models if self.models is not None else self.prof.models
        return getattr(models, role)


def provider_prefix(settings: Settings, *, server_tool: bool) -> str:
    """The pydantic-ai provider prefix for the current routing. ``server_tool`` marks roles
    that carry Anthropic server-side tools (they go direct under ``routing='split'``)."""
    direct = settings.routing == "direct" or (settings.routing == "split" and server_tool)
    return "anthropic:" if direct else "gateway/anthropic:"


def resolve_model(role: Role, settings: Settings) -> str:
    """Return the full pydantic-ai model string for a role, including routing prefix."""
    return provider_prefix(settings, server_tool=role in SERVER_TOOL_ROLES) + settings.bare_model(role)
