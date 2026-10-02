"""Export experiment results as OpenTelemetry traces (``pip install 'agentic-chaos-security[otel]'``).

One span per experiment, a child span per run (baseline and chaos), and the run's trace events as span
events. Attributes are redacted like JSON reports. With ``agentic-chaos-security run --otel`` and no tracer
provider configured, spans are sent with the OTLP/HTTP exporter, configured by the standard
``OTEL_EXPORTER_OTLP_*`` environment variables.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

try:
    from opentelemetry import trace
except ImportError as exc:  # pragma: no cover
    raise ImportError("install the otel extra: pip install 'agentic-chaos-security[otel]'") from exc

from agentic_chaos_security.experiment import ExperimentResult
from agentic_chaos_security.redact import Redactor, redact

__all__ = ["MAX_EVENTS", "export"]

#: Span events per run are capped to keep traces within collector limits.
MAX_EVENTS = 256


def _value(value: Any) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    text = json.dumps(value, default=str)
    return text if len(text) <= 2000 else text[:2000] + "..."


def export(
    results: Sequence[ExperimentResult],
    *,
    redactor: Redactor | bool = True,
    tracer_provider: Any = None,
) -> None:
    """Emit spans for ``results``. Uses the global tracer provider unless one is given."""
    provider = tracer_provider or _default_provider()
    tracer = provider.get_tracer("agentic_chaos_security")

    def clean(value: Any) -> Any:
        if redactor is False:
            return value
        return redact(value, None if redactor is True else redactor)

    for result in results:
        report = result.to_dict(redactor=redactor)
        attributes = {
            "chaos.experiment": report["name"],
            "chaos.verdict": report["verdict"],
            "chaos.reason": report["reason"],
            "chaos.hypothesis": report["hypothesis"],
            "chaos.tags": list(report["tags"]),
        }
        with tracer.start_as_current_span(f"chaos.experiment {report['name']}", attributes=attributes):
            for run in [*result.baseline, *result.chaos]:
                end = time.time_ns()
                start = end - int(run.trace.duration * 1e9)
                span = tracer.start_span(
                    f"chaos.run {run.phase} {run.index}",
                    start_time=start,
                    attributes={
                        "chaos.phase": run.phase,
                        "chaos.run": run.index,
                        "chaos.passed": run.passed,
                        "chaos.faults_fired": run.faults_fired,
                        "chaos.failed_probes": [p.name for p in run.probes if not p.passed],
                    },
                )
                for event in run.trace.events[:MAX_EVENTS]:
                    data = clean(event.data)
                    span.add_event(
                        event.kind,
                        attributes={
                            "chaos.name": event.name,
                            **{f"chaos.data.{k}": _value(v) for k, v in data.items()},
                        },
                    )
                if run.trace.error is not None:
                    span.set_attribute("chaos.error", clean(repr(run.trace.error)))
                span.end(end_time=end)
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        flush()


def _default_provider() -> Any:
    provider = trace.get_tracer_provider()
    if type(provider).__name__ != "ProxyTracerProvider":
        return provider
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover
        raise ImportError("install the otel extra: pip install 'agentic-chaos-security[otel]'") from exc
    sdk_provider = TracerProvider(resource=Resource.create({"service.name": "agentic-chaos"}))
    sdk_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return sdk_provider
