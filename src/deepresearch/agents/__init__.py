"""Pipeline agent definitions — prompts and output validators only; no control flow.

``researcher_agent`` is a cached factory (search cap is fixed at construction); the rest are
module-level agents. None bind a model — the orchestrator passes ``model=`` per run.
"""

from .clarifier import clarifier_agent
from .critic import critic_agent
from .gap_analyst import gap_analyst_agent
from .planner import planner_agent
from .researcher import researcher_agent
from .synthesizer import outline_agent, section_agent
from .verifier import verifier_agent

__all__ = [
    "clarifier_agent",
    "critic_agent",
    "gap_analyst_agent",
    "outline_agent",
    "planner_agent",
    "researcher_agent",
    "section_agent",
    "verifier_agent",
]
