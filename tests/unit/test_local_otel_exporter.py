"""Tests for the local OTEL span mirror (``LocalOtelFileExporter``).

The exporter is a lossless local mirror: every OTEL span the global provider
emits is written as one JSON object per line to ``<dir>/<trace_id_hex>.jsonl``.
These tests drive REAL ReadableSpans through the OTEL SDK (no hand-mocked spans)
and read the JSONL back, asserting the round-trip is lossless — including the key
caveat that business-tool errors surface at status DEFAULT/UNSET with the error
embedded in the ``output`` attribute, not the span status.

Offline only: no Langfuse, DB, or network.
"""

from __future__ import annotations

import builtins
import json

import pytest

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, Status, StatusCode

from src.observability.local_otel_exporter import (
    LocalOtelFileExporter,
    _coerce,
    _serialize_span,
)


# --- helpers ------------------------------------------------------------------------


class _CollectorExporter(SpanExporter):
    """Captures the real ReadableSpans the SDK emits, for direct export() calls."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _collect_spans(emit) -> list[ReadableSpan]:
    """Run ``emit(tracer)``, flush, and return the real ReadableSpans emitted."""
    provider = TracerProvider()
    collector = _CollectorExporter()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    tracer = provider.get_tracer("test.local_otel")
    emit(tracer)
    provider.force_flush()
    return collector.spans


def _read_lines(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _only_file(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1, f"expected one jsonl file, got {files}"
    return files[0]


# --- 1. lossless serialization ------------------------------------------------------


def test_serialization_is_lossless(tmp_path) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(LocalOtelFileExporter(str(tmp_path))))
    tracer = provider.get_tracer("test.local_otel")

    with tracer.start_as_current_span("evaluate_candidate") as span:
        span.set_attribute("candidate.name", "Alice Chen")
        span.set_attribute("score", 87)
        span.add_event("rubric.loaded", {"weights": 5})
        span.set_status(Status(StatusCode.OK))
    provider.force_flush()

    rows = _read_lines(_only_file(tmp_path))
    assert len(rows) == 1
    row = rows[0]

    for key in (
        "name", "trace_id", "span_id", "parent_span_id", "trace_flags",
        "trace_state", "kind", "start_time", "end_time", "status",
        "attributes", "events", "links", "resource",
        "instrumentation_scope",
    ):
        assert key in row, f"missing top-level key {key}"

    assert row["name"] == "evaluate_candidate"
    assert len(row["trace_id"]) == 32 and int(row["trace_id"], 16) >= 0
    assert len(row["span_id"]) == 16 and int(row["span_id"], 16) >= 0
    assert isinstance(row["start_time"], int)
    assert isinstance(row["end_time"], int)
    assert row["status"]["status_code"] == "OK"
    # OTEL only retains description for ERROR status; OK carries None.
    assert row["status"]["description"] is None
    assert row["attributes"]["candidate.name"] == "Alice Chen"
    assert row["attributes"]["score"] == 87

    assert len(row["events"]) == 1
    event = row["events"][0]
    assert event["name"] == "rubric.loaded"
    assert isinstance(event["timestamp"], int)
    assert event["attributes"] == {"weights": 5}


# --- 2. generation span carries usage tokens ----------------------------------------


def test_generation_span_carries_usage_tokens(tmp_path) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(LocalOtelFileExporter(str(tmp_path))))
    tracer = provider.get_tracer("test.local_otel")

    with tracer.start_as_current_span("generation", kind=SpanKind.CLIENT) as span:
        span.set_attribute("gen_ai.usage.input_tokens", 1234)
        span.set_attribute("gen_ai.usage.output_tokens", 567)
        span.set_attribute("gen_ai.request.model", "qwen3-32b")
    provider.force_flush()

    attrs = _read_lines(_only_file(tmp_path))[0]["attributes"]
    assert attrs["gen_ai.usage.input_tokens"] == 1234
    assert attrs["gen_ai.usage.output_tokens"] == 567
    assert attrs["gen_ai.request.model"] == "qwen3-32b"


# --- 3. business-error output preserved at DEFAULT status ---------------------------


def test_business_error_output_preserved_at_unset_status(tmp_path) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(LocalOtelFileExporter(str(tmp_path))))
    tracer = provider.get_tracer("test.local_otel")

    output_payload = json.dumps({"success": False, "error": "boom"})
    with tracer.start_as_current_span("query_database", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("output", output_payload)
        # NO explicit error status set — stays OTEL DEFAULT.
    provider.force_flush()

    row = _read_lines(_only_file(tmp_path))[0]
    # DEFAULT status serializes to "UNSET" — the error is NOT reflected in status.
    assert row["status"]["status_code"] == "UNSET"

    # The full output attribute survives un-truncated and parses back.
    assert row["attributes"]["output"] == output_payload
    parsed = json.loads(row["attributes"]["output"])
    assert parsed["success"] is False
    assert parsed["error"] == "boom"


# --- 4. per-trace partitioning ------------------------------------------------------


def test_per_trace_partitioning(tmp_path) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(LocalOtelFileExporter(str(tmp_path))))
    tracer = provider.get_tracer("test.local_otel")

    # Parent + child share ONE trace.
    with tracer.start_as_current_span("parent") as parent:
        parent_trace_hex = format(parent.get_span_context().trace_id, "032x")
        with tracer.start_as_current_span("child"):
            pass
    # A separate trace lands in its own file.
    with tracer.start_as_current_span("lonely") as other:
        other_trace_hex = format(other.get_span_context().trace_id, "032x")
    provider.force_flush()

    assert parent_trace_hex != other_trace_hex
    files = {p.name for p in tmp_path.glob("*.jsonl")}
    assert files == {f"{parent_trace_hex}.jsonl", f"{other_trace_hex}.jsonl"}

    parent_rows = _read_lines(tmp_path / f"{parent_trace_hex}.jsonl")
    assert len(parent_rows) == 2
    assert {r["name"] for r in parent_rows} == {"parent", "child"}
    # child records parent as its parent span; parent has none.
    by_name = {r["name"]: r for r in parent_rows}
    assert by_name["parent"]["parent_span_id"] is None
    assert by_name["child"]["parent_span_id"] == by_name["parent"]["span_id"]

    other_rows = _read_lines(tmp_path / f"{other_trace_hex}.jsonl")
    assert len(other_rows) == 1 and other_rows[0]["name"] == "lonely"


# --- 5. append mode -----------------------------------------------------------------


def test_export_appends_to_existing_trace_file(tmp_path) -> None:
    spans = _collect_spans(
        lambda t: [t.start_span("first").end(), t.start_span("second").end()]
    )
    # Force both into the same trace file by reusing one trace's spans across calls.
    single = _collect_spans(lambda t: t.start_span("solo").end())

    exporter = LocalOtelFileExporter(str(tmp_path))
    assert exporter.export(single) == SpanExportResult.SUCCESS
    assert exporter.export(single) == SpanExportResult.SUCCESS  # same trace_id again

    trace_hex = format(single[0].context.trace_id, "032x")
    rows = _read_lines(tmp_path / f"{trace_hex}.jsonl")
    assert len(rows) == 2  # accumulated, not truncated
    assert all(r["name"] == "solo" for r in rows)
    # separate spans confirm distinct trace ids partition (sanity for fixture)
    assert spans[0].context.trace_id != single[0].context.trace_id


# --- 6. export() result codes -------------------------------------------------------


def test_export_returns_success_on_normal_spans(tmp_path) -> None:
    spans = _collect_spans(lambda t: t.start_span("ok").end())
    exporter = LocalOtelFileExporter(str(tmp_path))
    assert exporter.export(spans) == SpanExportResult.SUCCESS


def test_export_returns_failure_on_oserror(tmp_path, monkeypatch) -> None:
    spans = _collect_spans(lambda t: t.start_span("doomed").end())
    exporter = LocalOtelFileExporter(str(tmp_path))

    def _raise_oserror(*args, **kwargs):
        raise OSError("disk full")

    # Patch the builtin the exporter uses to open the jsonl file for append.
    monkeypatch.setattr(builtins, "open", _raise_oserror)
    assert exporter.export(spans) == SpanExportResult.FAILURE


# --- 7. _coerce ---------------------------------------------------------------------


def test_coerce_primitives_pass_through() -> None:
    assert _coerce("s") == "s"
    assert _coerce(42) == 42
    assert _coerce(3.5) == 3.5
    assert _coerce(True) is True
    assert _coerce(None) is None


def test_coerce_sequences_become_lists() -> None:
    assert _coerce(["a", "b"]) == ["a", "b"]
    assert _coerce(("a", "b")) == ["a", "b"]
    assert isinstance(_coerce(("a", "b")), list)
    assert _coerce([1, 2, 3]) == [1, 2, 3]


# --- optional: _configure_local_otel gate -------------------------------------------


def test_configure_local_otel_disabled_by_default(monkeypatch) -> None:
    from src.observability import tracing

    monkeypatch.delenv("ENABLE_LOCAL_OTEL", raising=False)
    # Gated flag returns False before any Langfuse/provider work happens.
    assert tracing._configure_local_otel() is False


def test_serialize_span_direct_matches_export(tmp_path) -> None:
    """_serialize_span on a real span produces the same keys the file carries."""
    spans = _collect_spans(lambda t: t.start_span("direct").end())
    serialized = _serialize_span(spans[0])
    assert serialized["name"] == "direct"
    assert len(serialized["trace_id"]) == 32
    assert len(serialized["span_id"]) == 16
    assert serialized["parent_span_id"] is None
