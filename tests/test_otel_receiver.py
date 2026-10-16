"""Unit tests for scripts/otel/otlp_receiver.py.

Tests pure functions only -- no real HTTP server is started, no port is bound.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "otel"))
import otlp_receiver  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers to build minimal OTLP/JSON payloads
# ---------------------------------------------------------------------------


def _make_span(
    name: str = "tool_use",
    start_ns: int = 1_000_000_000_000_000_000,
    end_ns: int = 1_000_000_100_000_000_000,
    attributes: list | None = None,
    status_code: int | None = None,
) -> dict:
    span: dict = {
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "traceId": "abc123",
        "attributes": attributes or [],
    }
    if status_code is not None:
        span["status"] = {"code": status_code}
    return span


def _make_payload(
    spans: list[dict],
    resource_attrs: list | None = None,
) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs or []},
                "scopeSpans": [
                    {
                        "spans": spans,
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# normalize_otlp_traces -- basic span -> record
# ---------------------------------------------------------------------------


def test_normalize_basic_span():
    """A minimal span produces a record with expected fields."""
    start_ns = 1_700_000_000_000_000_000  # ~2023-11-14 22:13:20 UTC
    end_ns = start_ns + 200_000_000  # +200 ms
    payload = _make_payload([_make_span("tool_use", start_ns, end_ns)])

    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert len(records) == 1
    r = records[0]
    assert r["source"] == "dev"
    assert r["duration_ms"] == pytest.approx(200.0)
    assert r["success"] is True
    assert "ts" in r
    assert "span_type" in r
    assert "session_id" in r


def test_normalize_duration_from_nano():
    """duration_ms is correctly computed from nanosecond timestamps."""
    start_ns = 1_700_000_000_000_000_000
    end_ns = start_ns + 1_500_000_000  # +1500 ms
    payload = _make_payload([_make_span(start_ns=start_ns, end_ns=end_ns)])

    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["duration_ms"] == pytest.approx(1500.0)


def test_normalize_source_from_service_name():
    """source=bob derived from service.name resource attribute."""
    payload = _make_payload(
        [_make_span()],
        resource_attrs=[
            {"key": "service.name", "value": {"stringValue": "bob-agent"}}
        ],
    )
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["source"] == "bob"


def test_normalize_source_default_dev():
    """source=dev when service.name has no 'bob' in it."""
    payload = _make_payload(
        [_make_span()],
        resource_attrs=[
            {"key": "service.name", "value": {"stringValue": "claude-code"}}
        ],
    )
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["source"] == "dev"


def test_normalize_tool_name_from_attribute():
    """tool_name extracted from span attributes."""
    attrs = [{"key": "tool.name", "value": {"stringValue": "Read"}}]
    payload = _make_payload([_make_span(attributes=attrs)])

    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0].get("tool_name") == "Read"


def test_normalize_no_tool_name_attribute_omitted():
    """record without tool_name attribute does not include tool_name key."""
    payload = _make_payload([_make_span()])

    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert "tool_name" not in records[0]


def test_normalize_success_false_on_error_status():
    """Status code 2 (ERROR) maps to success=False."""
    payload = _make_payload([_make_span(status_code=2)])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["success"] is False


def test_normalize_success_true_on_ok_status():
    """Status code 1 (OK) maps to success=True."""
    payload = _make_payload([_make_span(status_code=1)])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["success"] is True


def test_normalize_cost_and_tokens():
    """cost_usd and tokens extracted from attributes when present."""
    attrs = [
        {"key": "llm.usage.cost_usd", "value": {"doubleValue": 0.0025}},
        {"key": "llm.usage.total_tokens", "value": {"intValue": 500}},
    ]
    payload = _make_payload([_make_span(attributes=attrs)])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    r = records[0]
    assert r.get("cost_usd") == pytest.approx(0.0025)
    assert r.get("tokens") == 500


def test_normalize_span_type_session():
    """Span name containing 'session' -> span_type='session'."""
    payload = _make_payload([_make_span(name="claude.session.start")])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["span_type"] == "session"


def test_normalize_span_type_tool_use():
    """Span name containing 'tool' -> span_type='tool_use'."""
    payload = _make_payload([_make_span(name="tool_invocation")])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["span_type"] == "tool_use"


def test_normalize_span_type_api_request():
    """Span name containing 'api' -> span_type='api_request'."""
    payload = _make_payload([_make_span(name="api_call")])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["span_type"] == "api_request"


def test_normalize_empty_payload():
    """Empty payload produces no records."""
    records = otlp_receiver.normalize_otlp_traces({}, source="dev")
    assert records == []


def test_normalize_multiple_spans():
    """Multiple spans in one payload produce multiple records."""
    spans = [_make_span(name=f"span-{i}") for i in range(3)]
    payload = _make_payload(spans)
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert len(records) == 3


def test_normalize_missing_start_time_skipped():
    """Span without startTimeUnixNano is skipped (returns empty list)."""
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [{"spans": [{"name": "bad", "attributes": []}]}],
            }
        ]
    }
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records == []


def test_normalize_session_id_from_attribute():
    """session_id extracted from span attribute."""
    attrs = [{"key": "session.id", "value": {"stringValue": "sess-abc-123"}}]
    payload = _make_payload([_make_span(attributes=attrs)])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["session_id"] == "sess-abc-123"


def test_normalize_session_id_fallback_trace_id():
    """session_id falls back to traceId when no session attribute."""
    payload = _make_payload([_make_span()])
    records = otlp_receiver.normalize_otlp_traces(payload, source="dev")
    assert records[0]["session_id"] == "abc123"


# ---------------------------------------------------------------------------
# append_spans -- JSONL write behavior
# ---------------------------------------------------------------------------


def test_append_spans_writes_one_line_per_record(tmp_path):
    """Each record produces exactly one line in the output file."""
    out = tmp_path / "spans.jsonl"
    records = [
        {"ts": "2026-01-01T00:00:00Z", "span_type": "tool_use", "source": "dev"},
        {"ts": "2026-01-01T00:00:01Z", "span_type": "api_request", "source": "dev"},
    ]
    otlp_receiver.append_spans(records, out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["span_type"] == "tool_use"
    assert json.loads(lines[1])["span_type"] == "api_request"


def test_append_spans_appends_not_overwrites(tmp_path):
    """Second call appends; first call's data is not lost."""
    out = tmp_path / "spans.jsonl"
    r1 = [{"ts": "2026-01-01T00:00:00Z", "span_type": "tool_use", "source": "dev"}]
    r2 = [{"ts": "2026-01-01T00:00:01Z", "span_type": "session", "source": "bob"}]

    otlp_receiver.append_spans(r1, out)
    otlp_receiver.append_spans(r2, out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    types = {json.loads(ln)["span_type"] for ln in lines}
    assert types == {"tool_use", "session"}


def test_append_spans_empty_list_does_not_create_file(tmp_path):
    """append_spans with empty list creates no file."""
    out = tmp_path / "spans.jsonl"
    otlp_receiver.append_spans([], out)
    assert not out.exists()


def test_append_spans_write_error_does_not_raise(tmp_path):
    """append_spans does not propagate exceptions from I/O errors."""
    # Point to a path that cannot be created (file as parent dir)
    bad_dir = tmp_path / "a_file.txt"
    bad_dir.write_text("x")
    bad_out = bad_dir / "spans.jsonl"  # file inside a file -> OS error
    # Must not raise
    otlp_receiver.append_spans([{"ts": "x"}], bad_out)


def test_append_spans_ensure_ascii_false(tmp_path):
    """Polish characters are stored unescaped (ensure_ascii=False)."""
    out = tmp_path / "spans.jsonl"
    records = [{"ts": "2026-01-01T00:00:00Z", "note": "zrodlo: narzedzia"}]
    otlp_receiver.append_spans(records, out)
    raw = out.read_bytes()
    # 'z' + 'r' + 0xC3 0xB3 ('o' with acute) present as UTF-8, not escaped as ó
    assert b"\\u" not in raw
