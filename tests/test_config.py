import pytest

from deepresearch.config import (
    PROFILES,
    Settings,
    _retrying_model,
    model_for_run,
    resolve_model,
    role_model_settings,
)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_profile_defaults():
    s = _settings()
    assert s.profile == "standard"
    assert s.routing == "gateway"
    assert s.effective_max_cost == PROFILES["standard"].default_max_cost


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="unknown profile"):
        _ = _settings(profile="nope").prof


@pytest.mark.parametrize(
    ("routing", "role", "expected_prefix"),
    [
        ("gateway", "planner", "gateway/anthropic:"),
        ("gateway", "researcher", "gateway/anthropic:"),
        ("direct", "planner", "anthropic:"),
        ("direct", "researcher", "anthropic:"),
        ("split", "planner", "gateway/anthropic:"),
        ("split", "researcher", "anthropic:"),  # server-tool role goes direct under split
        ("split", "verifier", "anthropic:"),
        ("split", "synthesizer", "gateway/anthropic:"),
    ],
)
def test_routing_matrix(routing, role, expected_prefix):
    assert resolve_model(role, _settings(routing=routing)).startswith(expected_prefix)


def test_role_model_settings_cache_placement():
    s = _settings()  # standard profile
    for role in ("researcher", "verifier"):
        ms = role_model_settings(role, s)
        assert ms["anthropic_cache_instructions"] is True
        assert ms["anthropic_cache_tool_definitions"] is True
        assert "anthropic_thinking" not in ms
    syn = role_model_settings("synthesizer", s)
    assert syn["anthropic_cache_messages"] is True
    assert "anthropic_thinking" not in syn  # section calls don't reason


def test_role_model_settings_thinking_matrix():
    s = _settings()  # standard: planner=sonnet-4-6 (budgeted), critic=sonnet-5 (adaptive)
    p = role_model_settings("planner", s)
    assert p["anthropic_thinking"]["type"] == "enabled"
    assert p["max_tokens"] > p["anthropic_thinking"]["budget_tokens"]
    c = role_model_settings("critic", s)
    assert c["anthropic_thinking"] == {"type": "adaptive"}
    assert c["anthropic_effort"] == "high"
    # Haiku researcher gets no thinking even if a caller asks for reasoning.
    r = role_model_settings("researcher", s, reasoning=True)
    assert "anthropic_thinking" not in r
    # The synthesizer's outline call: reasoning=True layers thinking onto message caching.
    outline = role_model_settings("synthesizer", s, reasoning=True)
    assert outline["anthropic_cache_messages"] is True
    assert outline["anthropic_thinking"] == {"type": "adaptive"}


def test_model_for_run_string_fallbacks(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "PYDANTIC_AI_GATEWAY_API_KEY", "PAIG_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _retrying_model.cache_clear()
    s = _settings()
    # No credentials in the process -> plain string, same label resolve_model would give.
    assert model_for_run("planner", s) == resolve_model("planner", s)
    # Feature disabled -> plain string, no construction attempted.
    off = _settings(transport_retries=False)
    assert model_for_run("planner", off) == resolve_model("planner", off)


def test_model_for_run_builds_retrying_model_when_keyed(monkeypatch):
    from pydantic_ai.models.anthropic import AnthropicModel

    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "fake-key")
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_BASE_URL", "https://gateway.example.test/proxy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-direct-key")
    _retrying_model.cache_clear()
    try:
        via_gateway = model_for_run("planner", _settings())
        assert isinstance(via_gateway, AnthropicModel)
        assert via_gateway.client.max_retries == 0  # tenacity transport owns retries, not the SDK
        direct = model_for_run("researcher", _settings(routing="split"))
        assert isinstance(direct, AnthropicModel)
        assert direct.client.max_retries == 0
    finally:
        _retrying_model.cache_clear()  # don't leak fake-keyed models to other tests


def test_per_role_env_override(monkeypatch):
    monkeypatch.setenv("DEEPRESEARCH_MODELS__PLANNER", "claude-opus-4-8")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__RESEARCHER", "claude-haiku-4-5")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__GAP_ANALYST", "claude-sonnet-4-6")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__VERIFIER", "claude-haiku-4-5")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__SYNTHESIZER", "claude-sonnet-5")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__CRITIC", "claude-opus-4-8")
    s = Settings(_env_file=None)
    assert resolve_model("planner", s) == "gateway/anthropic:claude-opus-4-8"
    assert resolve_model("critic", s) == "gateway/anthropic:claude-opus-4-8"
