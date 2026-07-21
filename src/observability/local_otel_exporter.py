"""Local OTEL span mirror — append raw spans to per-trace JSONL files.

A ``SpanExporter`` that captures every OTEL span the global TracerProvider
emits (Langfuse v4 registers its processor there) and writes each span as one
JSON object per line to ``<output_dir>/<trace_id_hex>.jsonl``.

The goal is a lossless local mirror: everything OTEL carries — attributes,
events, links, status, resource, instrumentation scope — is serialized without
truncation. Business-tool errors surface at status DEFAULT with the error
embedded in the output attribute, so attribute values are written in full.

Gated behind ``ENABLE_LOCAL_OTEL``; wired in ``src.observability.tracing``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from src.observability.logging import get_logger

log = get_logger(__name__)


def _coerce(value: Any) -> Any:
    """Coerce an attribute value into something JSON-safe.

    OTEL attribute values are primitives or homogeneous sequences of
    primitives, but be defensive: sequences are copied to lists, everything
    else falls through to ``json.dumps(default=str)`` at write time.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    return value


def _serialize_span(span: ReadableSpan) -> dict[str, Any]:
    """Serialize a ReadableSpan into a lossless JSON-safe dict."""
    ctx = span.context
    parent = span.parent

    scope = span.instrumentation_scope
    scope_dict = None
    if scope is not None:
        scope_dict = {
            "name": scope.name,
            "version": scope.version,
            "schema_url": scope.schema_url,
        }

    resource = span.resource
    resource_dict: dict[str, Any] = {
        "attributes": dict(resource.attributes) if resource is not None else {},
    }
    if resource is not None and getattr(resource, "schema_url", None):
        resource_dict["schema_url"] = resource.schema_url

    events = [
        {
            "name": event.name,
            "timestamp": event.timestamp,
            "attributes": {k: _coerce(v) for k, v in dict(event.attributes).items()},
        }
        for event in span.events
    ]

    links = [
        {
            "trace_id": format(link.context.trace_id, "032x"),
            "span_id": format(link.context.span_id, "016x"),
            "attributes": {k: _coerce(v) for k, v in dict(link.attributes).items()},
        }
        for link in span.links
    ]

    status = span.status
    attributes = span.attributes or {}

    out: dict[str, Any] = {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent is not None else None,
        "trace_flags": int(ctx.trace_flags),
        "trace_state": str(ctx.trace_state),
        "kind": span.kind.name,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "status": {
            "status_code": status.status_code.name,
            "description": status.description,
        },
        "attributes": {k: _coerce(v) for k, v in dict(attributes).items()},
        "events": events,
        "links": links,
        "resource": resource_dict,
        "instrumentation_scope": scope_dict,
        "dropped_attributes": getattr(span, "dropped_attributes", None),
        "dropped_events": getattr(span, "dropped_events", None),
        "dropped_links": getattr(span, "dropped_links", None),
    }
    return out


class LocalOtelFileExporter(SpanExporter):
    """Append each span as one JSON line to a per-trace JSONL file."""

    def __init__(self, output_dir: str) -> None:
        self._output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        # Group by trace file so each file is opened once per batch.
        by_trace: dict[str, list[str]] = defaultdict(list)
        try:
            for span in spans:
                trace_hex = format(span.context.trace_id, "032x")
                line = json.dumps(_serialize_span(span), default=str)
                by_trace[trace_hex].append(line)

            for trace_hex, lines in by_trace.items():
                path = os.path.join(self._output_dir, f"{trace_hex}.jsonl")
                with open(path, "a", encoding="utf-8") as fh:
                    for line in lines:
                        fh.write(line)
                        fh.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            log.warning("local_otel: export failed: %s", exc)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        # File writes are synchronous; nothing buffered to flush.
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        # Nothing buffered — writes complete inside export().
        return True
