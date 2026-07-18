"""Typer CLI entry point. Phase 1 wires the real pipeline; this stub keeps the script valid."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, rich_markup_mode="rich")


@app.command()
def research(question: str) -> None:
    """Run deep research on QUESTION (pipeline lands in Phase 1)."""
    typer.echo(f"deepresearch scaffold ready; pipeline not built yet. Question was: {question!r}")


if __name__ == "__main__":
    app()
