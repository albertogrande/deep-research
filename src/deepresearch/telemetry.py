"""Logfire setup. One call at process start; a no-op sender when LOGFIRE_TOKEN is absent."""

from __future__ import annotations

import logfire

_configured = False


def setup_telemetry(*, console: bool = False) -> None:
    """Configure Logfire and instrument pydantic-ai (idempotent).

    ``send_to_logfire='if-token-present'`` means: with LOGFIRE_TOKEN set, full traces stream
    to Logfire; without it, instrumentation still runs but nothing leaves the process, so the
    app works keyless. ``console=False`` keeps span logging from fighting the Rich Live UI.
    """
    global _configured
    if _configured:
        return
    logfire.configure(
        service_name="deepresearch",
        send_to_logfire="if-token-present",
        console=logfire.ConsoleOptions() if console else False,
    )
    logfire.instrument_pydantic_ai()
    _configured = True


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or None when no real trace is active (e.g. no
    LOGFIRE_TOKEN). Lets a run.json link back to its Logfire trace — borrowed from the
    semantica-ai plan's ``execution_id = root span trace id`` pattern."""
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.trace_id else None
