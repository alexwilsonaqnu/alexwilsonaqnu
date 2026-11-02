"""OpenTelemetry via Pydantic Logfire. One trace per pipeline run.

Logfire is OTel-native: spans created here are standard OTel spans, so switching backends later
means changing exporter config, not instrumentation.
"""

from __future__ import annotations

import logfire
from opentelemetry import trace

from solbrief.config import get_settings

_configured = False


def configure(service: str) -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    logfire.configure(
        service_name=f"solbrief-{service}",
        environment=s.app_env,
        token=s.logfire_token,
        send_to_logfire="if-token-present",  # local dev/tests: no export, spans still created
        console=False,
    )
    logfire.instrument_psycopg()
    logfire.instrument_httpx()
    _configured = True


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
