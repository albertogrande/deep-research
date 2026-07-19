"""Shared fixtures: offline settings and scripted models. No test in this suite hits the network."""

from __future__ import annotations

import os

import logfire
import pytest
from pydantic_ai.models.test import TestModel

from deepresearch.config import Settings

# Keep the offline suite hermetic against an ambient LOGFIRE_TOKEN. The orchestrator calls
# `setup_telemetry()`, which configures Logfire with `send_to_logfire="if-token-present"`. If the
# developer's shell exports a LOGFIRE_TOKEN (even an invalid one), that resolves to "send", so the
# `logfire.span(...)` calls in the code under test try to export spans/metrics — hitting the
# network and spewing 401s, breaking this module's "no test hits the network" promise. Dropping the
# token from the test environment makes `if-token-present` resolve to offline. No live test gates on
# LOGFIRE_TOKEN (only the Anthropic/Gateway keys), so this is safe for `RUN_LIVE_TESTS=1` too.
os.environ.pop("LOGFIRE_TOKEN", None)
# Belt-and-suspenders: force Logfire offline before any code-under-test configures it lazily.
logfire.configure(send_to_logfire=False, console=False)


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
}


@pytest.fixture
def planner_model() -> TestModel:
    return TestModel(custom_output_args=PLAN_ARGS)


@pytest.fixture
def researcher_model() -> TestModel:
    return TestModel(custom_output_args=FINDINGS_ARGS)
