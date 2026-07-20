"""Shared fixtures: offline settings and scripted models. No test in this suite hits the network."""

from __future__ import annotations

import os

import pytest
from pydantic_ai.models.test import TestModel

from deepresearch.config import Settings, _retrying_model


@pytest.fixture(autouse=True)
def _offline_no_credentials(monkeypatch):
    """Hermetic offline suite: strip provider credentials so `model_for_run` always falls
    back to a model string and an un-overridden agent fails fast instead of making a real
    API call. Dev environments legitimately export real keys (journal 13) — without this,
    any agent a test forgot to override would silently hit the network. Live tests
    (RUN_LIVE_TESTS=1) keep their credentials."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        yield
        return
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "PYDANTIC_AI_GATEWAY_API_KEY",
        "PYDANTIC_AI_GATEWAY_BASE_URL",
        "PAIG_API_KEY",
        "PAIG_BASE_URL",
        "LOGFIRE_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    _retrying_model.cache_clear()  # a model cached while keys existed would still be live
    yield
    _retrying_model.cache_clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Quick-profile settings writing artifacts into a temp dir; env/.env ignored."""
    return Settings(_env_file=None, profile="quick", output_dir=str(tmp_path / "runs"))


PLAN_ARGS = {
    "sub_questions": [
        {"question": "What is the capital of France?", "rationale": "core fact"},
        {"question": "What is the population of Paris?", "rationale": "supporting fact"},
    ],
    "done_criteria": ["capital named", "population stated"],
}

FINDINGS_ARGS = {
    "claims": [
        {
            "statement": "Paris is the capital of France.",
            "supporting_quote": "Paris is the capital and largest city of France.",
            "source_url": "https://en.wikipedia.org/wiki/Paris",
            "source_title": "Paris - Wikipedia",
            "confidence": "high",
        },
        {
            "statement": "Paris has about 2.1 million inhabitants.",
            "supporting_quote": "The City of Paris had a population of 2,102,650.",
            "source_url": "https://en.wikipedia.org/wiki/Paris",
            "source_title": "Paris - Wikipedia",
            "confidence": "medium",
        },
    ],
    "search_queries_used": ["capital of France"],
    "notes": "",
    "summary": "Established the capital and its population; no open questions.",
}


@pytest.fixture
def planner_model() -> TestModel:
    return TestModel(custom_output_args=PLAN_ARGS)


@pytest.fixture
def researcher_model() -> TestModel:
    return TestModel(custom_output_args=FINDINGS_ARGS)
