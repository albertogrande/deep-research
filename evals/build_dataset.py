"""Build and serialize the eval dataset (run once; dataset.yaml is committed).

uv run python -m evals.build_dataset
"""

from __future__ import annotations

from pathlib import Path

from pydantic_evals import Case, Dataset

from .common import EvalMeta, EvalOutput

CASES: list[Case[str, EvalOutput, EvalMeta]] = [
    Case(
        name="factual-lookup",
        inputs="When did the EU AI Act enter into force, and when do its GPAI obligations start to apply?",
        metadata=EvalMeta(category="factual", notes="checkable dates; official sources exist"),
    ),
    Case(
        name="multi-hop-comparison",
        inputs="Compare the pricing and context windows of the current Anthropic and OpenAI flagship models.",
        metadata=EvalMeta(category="multi-hop", notes="requires combining two vendors' current docs"),
    ),
    Case(
        name="recent-events",
        inputs="What were the major announcements at the most recent Apple WWDC?",
        metadata=EvalMeta(category="time-sensitive", notes="freshness matters; date anchoring"),
    ),
    Case(
        name="numeric-statistical",
        inputs="What is the current global installed solar PV capacity and how fast is it growing year over year?",
        metadata=EvalMeta(category="numeric", notes="numbers must be cited and hedged appropriately"),
    ),
    Case(
        name="contested-topic",
        inputs="What does the current evidence say about the health effects of moderate alcohol consumption?",
        metadata=EvalMeta(category="contested", notes="multiple sides without false balance"),
    ),
    Case(
        name="niche-technical",
        inputs="How does PostgreSQL's MVCC handle transaction ID wraparound, and what operational safeguards exist?",
        metadata=EvalMeta(category="technical", notes="depth over breadth; docs are primary source"),
    ),
    Case(
        name="broad-survey",
        inputs="What are the main approaches to detecting hallucinations in large language model outputs?",
        metadata=EvalMeta(category="survey", notes="coverage of distinct method families"),
    ),
    Case(
        name="false-premise",
        inputs="Why did Python 4 remove the GIL?",
        metadata=EvalMeta(
            category="false-premise",
            notes="there is no Python 4; free-threading landed in 3.13+. Report must correct the premise",
        ),
    ),
]


def build() -> Dataset[str, EvalOutput, EvalMeta]:
    return Dataset[str, EvalOutput, EvalMeta](name="deepresearch-v1", cases=CASES)


if __name__ == "__main__":
    out = Path(__file__).parent / "dataset.yaml"
    build().to_file(out)
    print(f"wrote {out} (+ companion schema)")
