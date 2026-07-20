"""User-interaction hooks for human-in-the-loop runs.

The orchestrator calls these at exactly two points — clarification before planning, and plan
review after planning but before any researcher spends money. It never knows what UI sits
behind them (the CLI provides console prompts; a server could provide anything else), the same
way progress events keep the orchestrator UI-agnostic.
"""

from __future__ import annotations

from typing import Protocol

from .models import ResearchPlan


class InteractionHooks(Protocol):
    async def clarify(self, questions: list[str]) -> list[str]:
        """Answer the clarifier's questions, one answer per question (empty string = skip)."""
        ...

    async def review_plan(self, plan: ResearchPlan) -> ResearchPlan:
        """Show the plan; return it (possibly edited) to approve. Raise to abort pre-spend."""
        ...
