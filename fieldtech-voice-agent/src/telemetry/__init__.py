"""OTel from day one.

`span()` wraps every tool call, model call, agent turn, STT and TTS call. It ALWAYS
appends a JSON line to observability/traces/spans.jsonl, and additionally exports OTLP
when OTEL_EXPORTER_OTLP_ENDPOINT is set.

Telemetry never fails the call path. A broken exporter must not drop a technician's call.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from src.config import TRACES_PATH

_write_lock = threading.Lock()
_otel_tracer = None
_otel_initialized = False


def _tracer():
    """Lazily build an OTLP tracer. Any failure degrades to file-only tracing."""
    global _otel_tracer, _otel_initialized
    if _otel_initialized:
        return _otel_tracer
    _otel_initialized = True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "fieldtech-assist")})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _otel_tracer = trace.get_tracer("fieldtech-assist")
    except Exception:  # exporter unavailable / misconfigured -> file-only
        _otel_tracer = None
    return _otel_tracer


def _append(record: dict[str, Any]) -> None:
    try:
        TRACES_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, default=str)
        with _write_lock:
            with TRACES_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass  # never fail the call path


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Context manager yielding a mutable attribute dict.

    Mutate the yielded dict to attach results:  with span("tool", tool=n) as s: s["hits"]=3
    """
    span_id = uuid.uuid4().hex[:16]
    attrs: dict[str, Any] = dict(attributes)
    started = time.time()
    otel_span_cm = None
    otel_span = None
    tracer = _tracer()
    if tracer is not None:
        try:
            otel_span_cm = tracer.start_as_current_span(name)
            otel_span = otel_span_cm.__enter__()
        except Exception:
            otel_span_cm = otel_span = None

    status = "ok"
    error: str | None = None
    try:
        yield attrs
    except BaseException as exc:
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = round((time.time() - started) * 1000, 2)
        _append(
            {
                "span_id": span_id,
                "name": name,
                "status": status,
                "error": error,
                "started_at": started,
                "duration_ms": duration_ms,
                "attributes": attrs,
            }
        )
        if otel_span is not None:
            try:
                for key, value in attrs.items():
                    otel_span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
                if error:
                    otel_span.set_attribute("error.message", error)
            except Exception:
                pass
        if otel_span_cm is not None:
            try:
                otel_span_cm.__exit__(None, None, None)
            except Exception:
                pass


__all__ = ["span"]
