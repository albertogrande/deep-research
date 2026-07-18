import pytest

from deepresearch.config import PROFILES, Settings, resolve_model


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


def test_per_role_env_override(monkeypatch):
    monkeypatch.setenv("DEEPRESEARCH_MODELS__PLANNER", "claude-opus-4-8")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__RESEARCHER", "claude-haiku-4-5")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__GAP_ANALYST", "claude-sonnet-4-6")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__VERIFIER", "claude-haiku-4-5")
    monkeypatch.setenv("DEEPRESEARCH_MODELS__SYNTHESIZER", "claude-sonnet-5")
    s = Settings(_env_file=None)
    assert resolve_model("planner", s) == "gateway/anthropic:claude-opus-4-8"
