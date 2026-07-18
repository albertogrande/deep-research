"""Typed progress events emitted by the orchestrator.

The orchestrator is UI-agnostic: it calls an ``on_event`` callback with these frozen
dataclasses. Phase 1 renders them as plain lines; the Rich Live UI arrives in Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    pass


@dataclass(frozen=True)
class PlanReady(ProgressEvent):
    n_sub_questions: int
    done_criteria: tuple[str, ...]


@dataclass(frozen=True)
class WaveStarted(ProgressEvent):
    wave: int
    n_questions: int


@dataclass(frozen=True)
class ResearcherStarted(ProgressEvent):
    sub_question_id: str
    question: str


@dataclass(frozen=True)
class ResearcherFinished(ProgressEvent):
    sub_question_id: str
    claims_found: int


@dataclass(frozen=True)
class ResearcherFailed(ProgressEvent):
    sub_question_id: str
    reason: str


@dataclass(frozen=True)
class GapResult(ProgressEvent):
    saturated: bool
    n_follow_ups: int


@dataclass(frozen=True)
class VerificationProgress(ProgressEvent):
    done: int
    total: int
    pass_rate: float


@dataclass(frozen=True)
class SynthesisStage(ProgressEvent):
    stage: str  # "outline" | "section"
    index: int = 0
    total: int = 0


@dataclass(frozen=True)
class CostUpdate(ProgressEvent):
    estimate_usd: float
    cap_usd: float


class LiveProgress:
    """Rich Live renderer consuming orchestrator events. Use as a context manager; pass
    ``.emit`` as the orchestrator's ``on_event`` callback."""

    def __init__(self, console, query: str, profile: str, routing: str) -> None:
        from rich.live import Live

        self._console = console
        self._query = query
        self._profile = profile
        self._routing = routing
        self._live = Live(console=console, refresh_per_second=8, transient=False)
        self._plan: PlanReady | None = None
        self._current_wave = 0
        # wave -> sq_id -> (question, status, claims_found)
        self._waves: dict[int, dict[str, tuple[str, str, int]]] = {}
        self._verification: VerificationProgress | None = None
        self._synthesis: str = ""
        self._cost: CostUpdate | None = None

    def __enter__(self) -> LiveProgress:
        self._live.__enter__()
        self._refresh()
        return self

    def __exit__(self, *exc_info) -> None:
        self._live.__exit__(*exc_info)

    def emit(self, event: ProgressEvent) -> None:
        match event:
            case PlanReady():
                self._plan = event
            case WaveStarted(wave=w):
                self._current_wave = w
                self._waves.setdefault(w, {})
            case ResearcherStarted(sub_question_id=sq_id, question=q):
                self._waves.setdefault(self._current_wave, {})[sq_id] = (q, "searching", 0)
            case ResearcherFinished(sub_question_id=sq_id, claims_found=n):
                q = self._find_question(sq_id)
                self._waves[self._current_wave][sq_id] = (q, "done", n)
            case ResearcherFailed(sub_question_id=sq_id, reason=r):
                q = self._find_question(sq_id)
                self._waves[self._current_wave][sq_id] = (q, f"failed ({r})", 0)
            case GapResult(saturated=sat, n_follow_ups=n):
                self._synthesis = "saturated — no further waves" if sat else f"gap analysis: {n} follow-ups"
            case VerificationProgress():
                self._verification = event
            case SynthesisStage(stage=stage, index=i, total=t):
                self._synthesis = "writing outline" if stage == "outline" else f"writing section {i}/{t}"
            case CostUpdate():
                self._cost = event
        self._refresh()

    def _find_question(self, sq_id: str) -> str:
        for wave in self._waves.values():
            if sq_id in wave:
                return wave[sq_id][0]
        return sq_id

    def _refresh(self) -> None:
        self._live.update(self._render())

    def _render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        parts: list = []
        header = Text.assemble(
            (self._query, "bold"),
            (f"\nprofile={self._profile} routing={self._routing}", "dim"),
        )
        if self._plan:
            header.append(f"\nplan: {self._plan.n_sub_questions} sub-questions", style="cyan")
        parts.append(Panel(header, title="deepresearch", border_style="blue"))

        status_icon = {"searching": "⋯", "done": "✓"}
        for wave_n in sorted(self._waves):
            lines = Text()
            for sq_id, (q, status, n) in self._waves[wave_n].items():
                icon = status_icon.get(status, "✗")
                style = "green" if status == "done" else ("red" if icon == "✗" else "yellow")
                suffix = f" — {n} claims" if status == "done" else (f" — {status}" if icon == "✗" else "")
                lines.append(f"{icon} {sq_id} ", style=style)
                lines.append(f"{q[:76]}{suffix}\n")
            parts.append(Panel(lines, title=f"wave {wave_n}", border_style="cyan"))

        footer = Text()
        if self._verification:
            v = self._verification
            footer.append(
                f"verification: {v.done}/{v.total} sources — {v.pass_rate:.0%} supported\n", style="magenta"
            )
        if self._synthesis:
            footer.append(f"{self._synthesis}\n", style="cyan")
        if self._cost:
            footer.append(
                f"cost ≈ ${self._cost.estimate_usd:.2f} / cap ${self._cost.cap_usd:.2f}", style="dim"
            )
        if footer.plain:
            parts.append(footer)
        return Group(*parts)
