"""Minimal OpenTelemetry tracing. No-op unless a provider is configured.

Set OTEL_CONSOLE=1 to print spans to the console; in production you'd point an
OTLP exporter at your collector instead.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_tracer = None


def setup_tracing() -> None:
    global _tracer
    try:
        from opentelemetry import trace

        if os.environ.get("OTEL_CONSOLE", "").lower() in ("1", "true", "yes"):
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                ConsoleSpanExporter,
                SimpleSpanProcessor,
            )

            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("incident-triage-agent")
    except Exception:
        _tracer = None


@contextmanager
def span(name: str):
    """Trace a phase. Silently no-ops if OTel isn't available."""
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name):
        yield
