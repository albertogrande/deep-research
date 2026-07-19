"""Settings, depth profiles, model pricing, and model-string resolution.

Model strings (including the ``gateway/`` vs ``anthropic:`` prefix) are spelled in
:func:`resolve_model` and nowhere else in the codebase. The same rule extends to model
*capability* knowledge: which models think adaptively, what gets prompt-cached per role,
and how transports retry all live HERE (:func:`role_model_settings` / :func:`model_for_run`).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    import httpx
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings

Role = Literal["planner", "researcher", "gap_analyst", "verifier", "synthesizer", "critic"]
Routing = Literal["gateway", "direct", "split"]

# $/MTok (input, output). FALLBACK ONLY — primary pricing comes from the genai-prices bundled
# snapshot (see deps.price_role_usage), which also handles cache tokens and the Sonnet 5
# intro→standard date transition. This table is used only for models genai-prices doesn't know.
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
    critic: str


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
            critic="claude-sonnet-4-6",
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
            critic="claude-sonnet-5",
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
            critic="claude-opus-4-8",
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
    max_revise_iters: int = 2  # critic-driven revise passes after the first draft (0 disables the loop)
    concurrency: int | None = None  # None -> profile default
    output_dir: str = "runs"
    transport_retries: bool = True  # tenacity transport with Retry-After handling under every model

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
    """Return the full pydantic-ai model string for a role, including routing prefix.

    This is the LABEL — it is what lands in ``RunRecord.models_used`` and what pricing keys
    off. :func:`model_for_run` may upgrade it to a ``Model`` object with a retrying transport,
    but the string identity of a run never changes."""
    return provider_prefix(settings, server_tool=role in SERVER_TOOL_ROLES) + settings.bare_model(role)


# --- per-role model settings: prompt caching + thinking --------------------------------------

# Roles whose calls are preceded by deliberate reasoning. They get thinking when their model
# supports it; researchers/verifiers/section-writers don't (cost floor matters more there).
REASONING_ROLES: frozenset[str] = frozenset({"planner", "gap_analyst", "critic"})

# Thinking needs output headroom: the pydantic-ai Anthropic default is max_tokens=4096, and a
# budgeted thinking config must fit strictly under max_tokens.
THINKING_MAX_TOKENS = 8192
THINKING_BUDGET_TOKENS = 3072

# Adaptive thinking (model decides when/how much, steered by `anthropic_effort`) exists on
# Sonnet 5 / Opus >= 4.7; older Sonnets use a fixed budget; Haiku runs without thinking.
_ADAPTIVE_THINKING_MODELS = ("claude-sonnet-5", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5")
_BUDGETED_THINKING_MODELS = ("claude-sonnet-4-6",)


def _thinking_settings(bare_model: str) -> dict:
    if bare_model.startswith(_ADAPTIVE_THINKING_MODELS):
        return {
            "anthropic_thinking": {"type": "adaptive"},
            "anthropic_effort": "high",
            "max_tokens": THINKING_MAX_TOKENS,
        }
    if bare_model.startswith(_BUDGETED_THINKING_MODELS):
        return {
            "anthropic_thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
            "max_tokens": THINKING_MAX_TOKENS,
        }
    return {}


def role_model_settings(
    role: Role, settings: Settings, *, reasoning: bool | None = None
) -> AnthropicModelSettings | None:
    """Model settings for one role: prompt-cache placement and thinking config.

    ``reasoning`` overrides the role default for calls where the orchestrator knows better
    (the synthesizer's outline call reasons; its section calls just write).
    Caching is always on — a cache read is strictly cheaper than a fresh read.
    """
    from pydantic_ai.models.anthropic import AnthropicModelSettings

    out: dict = {}
    if role in ("researcher", "verifier"):
        # Many calls per run share the same large instructions + server-tool definitions.
        out["anthropic_cache_instructions"] = True
        out["anthropic_cache_tool_definitions"] = True
    elif role == "synthesizer":
        # The outline digest recurs verbatim across section/revise calls; message-level
        # cache_control is the variant that survives gateways/proxies.
        out["anthropic_cache_messages"] = True
    if reasoning is None:
        reasoning = role in REASONING_ROLES
    if reasoning:
        out.update(_thinking_settings(settings.bare_model(role)))
    return AnthropicModelSettings(**out) if out else None


# --- transport-level retries -----------------------------------------------------------------


def _validate_response(response: httpx.Response) -> None:
    """Only convert retry-worthy statuses into exceptions; 4xx client errors flow through to
    the SDK untouched (retrying a 400 is pointless, and `classify_error` handles the rest)."""
    if response.status_code == 429 or response.status_code >= 500:
        response.raise_for_status()


@lru_cache(maxsize=1)
def _retrying_http_client() -> httpx.AsyncClient:
    """One shared httpx client whose transport retries 429/5xx/connection trouble with
    exponential backoff, honouring Retry-After (`wait_retry_after`)."""
    import httpx
    from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
    from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

    transport = AsyncTenacityTransport(
        RetryConfig(
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
            wait=wait_retry_after(fallback_strategy=wait_exponential(multiplier=1, max=30), max_wait=60),
            stop=stop_after_attempt(4),
            reraise=True,
        ),
        validate_response=_validate_response,
    )
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600.0, connect=10.0))


@lru_cache(maxsize=32)
def _retrying_model(bare_model: str, *, direct: bool) -> AnthropicModel:
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    from pydantic_ai.providers.gateway import gateway_provider

    client = _retrying_http_client()
    if direct:
        from anthropic import AsyncAnthropic

        # max_retries=0: the tenacity transport owns retrying; the SDK's own retry loop on
        # top of it would multiply attempts (documented pydantic-ai gotcha).
        provider = AnthropicProvider(anthropic_client=AsyncAnthropic(http_client=client, max_retries=0))
    else:
        provider = gateway_provider("anthropic", http_client=client)
        provider.client.max_retries = 0  # same gotcha; gateway_provider has no direct knob
    return AnthropicModel(bare_model, provider=provider)


def model_for_run(role: Role, settings: Settings) -> str | AnthropicModel:
    """What the orchestrator passes as ``model=``: a Model object carrying the retrying
    transport when credentials exist, else the plain :func:`resolve_model` string.

    The string fallback keeps offline tests key-free (``Agent.override`` ignores ``model=``)
    and leaves live misconfiguration to fail with pydantic-ai's own clear missing-key error."""
    if not settings.transport_retries:
        return resolve_model(role, settings)
    direct = settings.routing == "direct" or (settings.routing == "split" and role in SERVER_TOOL_ROLES)
    try:
        return _retrying_model(settings.bare_model(role), direct=direct)
    except Exception:
        return resolve_model(role, settings)
