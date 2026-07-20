"""deepresearch — a deep research agent system built on the Pydantic stack.

Library surface: ``run_research`` is the programmatic entry point; everything it needs and
returns is importable from here. The CLI (`deepresearch`) and MCP server (`deepresearch-mcp`)
are thin layers over the same call. Exports resolve lazily (PEP 562) so importing the package
— e.g. for `deepresearch --help` — doesn't pay for the whole pipeline.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings
    from .models import Checkpoint, RunRecord
    from .orchestrator import RunResult, run_research

try:
    __version__ = version("deepresearch")
except PackageNotFoundError:  # pragma: no cover — running from a raw source tree
    __version__ = "0.0.0.dev0"

__all__ = ["Checkpoint", "RunRecord", "RunResult", "Settings", "__version__", "run_research"]

_LAZY = {
    "Settings": ("deepresearch.config", "Settings"),
    "Checkpoint": ("deepresearch.models", "Checkpoint"),
    "RunRecord": ("deepresearch.models", "RunRecord"),
    "RunResult": ("deepresearch.orchestrator", "RunResult"),
    "run_research": ("deepresearch.orchestrator", "run_research"),
}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    return getattr(import_module(module_name), attr)
