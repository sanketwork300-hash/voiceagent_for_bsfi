"""OpenTelemetry tracing: one trace spans channel -> runtime -> LLM -> RAG/tool -> external BFSI API."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

log = logging.getLogger(__name__)
_configured = False


def setup_tracing(service_name: str, otlp_endpoint: str | None, enabled: bool = True) -> None:
    global _configured
    if _configured or not enabled:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str = "bfsi") -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Span helper. Attribute values must already be PII-safe (ids, enums, counts, latencies)."""
    with get_tracer().start_as_current_span(name) as s:
        for k, v in attributes.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        try:
            yield s
        except Exception as e:
            s.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def inject_headers(headers: dict[str, str]) -> dict[str, str]:
    """W3C traceparent propagation to downstream bank APIs / MCP servers."""
    from opentelemetry.propagate import inject

    inject(headers)
    return headers
