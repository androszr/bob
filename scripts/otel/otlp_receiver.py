"""OTLP/HTTP JSON receiver daemon for Claude Code telemetry.

Listens on 127.0.0.1:4318 (default), accepts POST /v1/traces and POST /v1/metrics,
normalizes spans to records and appends them to otel-spans.jsonl.

Usage:
    python scripts/otel/otlp_receiver.py [--host 127.0.0.1] [--port 4318]
        [--out scripts/otel/otel-spans.jsonl]
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_DEFAULT_OUT = Path(__file__).parent / "otel-spans.jsonl"


# ---------------------------------------------------------------------------
# Pure parsing / normalization (testable without I/O)
# ---------------------------------------------------------------------------


def _get_attr(attributes: list, key: str):
    """Extract value from OTLP attribute list [{key, value}] by key. Returns None if missing."""
    for item in attributes:
        if item.get("key") == key:
            val = item.get("value", {})
            # OTLP value is a typed union: stringValue, intValue, doubleValue, boolValue
            for vtype in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if vtype in val:
                    return val[vtype]
            return None
    return None


def _classify_span_type(span_name: str, attributes: list) -> str:
    """Classify span into session / tool_use / api_request based on name and attributes."""
    name_lower = span_name.lower()
    if "session" in name_lower:
        return "session"
    if "tool" in name_lower:
        return "tool_use"
    if "api" in name_lower or "request" in name_lower:
        return "api_request"
    # Fallback: check attribute keys for hints
    keys = {item.get("key", "") for item in attributes}
    if "tool.name" in keys or "tool_name" in keys:
        return "tool_use"
    return "api_request"


def _derive_source(resource_attributes: list, service_name_hint: str | None = None) -> str:
    """Derive source (bob / dev) from resource attributes or service.name hint."""
    svc = _get_attr(resource_attributes, "service.name") or service_name_hint or ""
    if "bob" in svc.lower():
        return "bob"
    return "dev"


def normalize_otlp_traces(payload: dict, source: str = "dev") -> list[dict]:
    """Parse OTLP/JSON traces payload and return a list of normalized span records.

    Each record has shape:
        {ts, span_type, session_id, tool_name?, duration_ms, success?, tokens?,
         cost_usd?, source}

    Defensive: unknown / missing fields are skipped silently (partial records still emitted).
    """
    records: list[dict] = []

    resource_spans = payload.get("resourceSpans") or []
    for rs in resource_spans:
        resource = rs.get("resource") or {}
        resource_attrs = resource.get("attributes") or []

        # Override source from resource attributes if service.name present
        effective_source = _derive_source(resource_attrs, source)

        scope_spans = rs.get("scopeSpans") or []
        for ss in scope_spans:
            spans = ss.get("spans") or []
            for span in spans:
                try:
                    record = _normalize_span(span, effective_source, resource_attrs)
                    if record is not None:
                        records.append(record)
                except Exception:
                    # Skip malformed span; do not crash the receiver.
                    pass

    return records


def _normalize_span(
    span: dict, source: str, resource_attrs: list
) -> dict | None:
    """Normalize a single OTLP span dict to our storage record. Returns None on fatal error."""
    start_ns = span.get("startTimeUnixNano")
    end_ns = span.get("endTimeUnixNano")

    if not start_ns:
        return None

    try:
        start_ns_int = int(start_ns)
        end_ns_int = int(end_ns) if end_ns else start_ns_int
        duration_ms = max(0.0, (end_ns_int - start_ns_int) / 1_000_000)
        # ISO timestamp from start time
        from datetime import UTC, datetime  # noqa: PLC0415
        ts_dt = datetime.fromtimestamp(start_ns_int / 1e9, tz=UTC)
        ts = ts_dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None

    attributes = span.get("attributes") or []
    span_name = span.get("name") or ""
    span_type = _classify_span_type(span_name, attributes)

    # session_id: prefer span attribute, fallback to traceId
    session_id = (
        _get_attr(attributes, "session.id")
        or _get_attr(attributes, "session_id")
        or _get_attr(resource_attrs, "session.id")
        or span.get("traceId")
        or ""
    )

    # Status: OTLP status code 1 = OK, 2 = ERROR; absent -> assume OK
    status = span.get("status") or {}
    status_code = status.get("code", 1)
    success = status_code != 2  # 2 = STATUS_CODE_ERROR

    record: dict = {
        "ts": ts,
        "span_type": span_type,
        "session_id": str(session_id),
        "duration_ms": round(duration_ms, 3),
        "success": success,
        "source": source,
    }

    # Optional fields: only include if present in attributes
    tool_name = (
        _get_attr(attributes, "tool.name")
        or _get_attr(attributes, "tool_name")
    )
    if tool_name is not None:
        record["tool_name"] = str(tool_name)

    tokens = _get_attr(attributes, "llm.usage.total_tokens") or _get_attr(
        attributes, "gen_ai.usage.input_tokens"
    )
    if tokens is not None:
        try:
            record["tokens"] = int(tokens)
        except (TypeError, ValueError):
            pass

    cost = _get_attr(attributes, "llm.usage.cost_usd") or _get_attr(
        attributes, "cost_usd"
    )
    if cost is not None:
        try:
            record["cost_usd"] = float(cost)
        except (TypeError, ValueError):
            pass

    return record


# ---------------------------------------------------------------------------
# JSONL append (standard repo pattern)
# ---------------------------------------------------------------------------


def append_spans(records: list[dict], out_path: Path) -> None:
    """Append normalized span records to JSONL file. Errors are logged, not raised."""
    if not records:
        return
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[otel] WARN: append_spans failed: {exc}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


def _make_handler(out_path: Path, default_source: str):
    """Factory returning a handler class bound to out_path and default_source."""

    class OTLPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: N802
            # Suppress default access logging to stderr to keep output clean.
            pass

        def _send(self, code: int, body: bytes = b"{}") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            path = self.path.rstrip("/")
            if path not in ("/v1/traces", "/v1/metrics"):
                self._send(404, b'{"error":"not found"}')
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except Exception as exc:
                print(f"[otel] WARN: JSON parse error: {exc}", file=sys.stderr, flush=True)
                self._send(400, b'{"error":"invalid json"}')
                return

            # Determine source from X-Source header or service.name
            source_header = self.headers.get("X-Source", "").strip() or default_source

            try:
                if path == "/v1/traces":
                    records = normalize_otlp_traces(payload, source=source_header)
                    append_spans(records, out_path)
                    print(
                        f"[otel] /v1/traces: {len(records)} span(s) -> {out_path}",
                        flush=True,
                    )
                else:
                    # /v1/metrics: accept but do not process (out of scope)
                    print("[otel] /v1/metrics: received (no-op)", flush=True)
            except Exception as exc:
                print(f"[otel] WARN: handler error: {exc}", file=sys.stderr, flush=True)
                # Still return 200 so the client doesn't retry indefinitely.

            self._send(200)

    return OTLPHandler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="OTLP/HTTP JSON receiver for Claude Code spans")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument(
        "--out",
        default=str(_DEFAULT_OUT),
        help="Path to output JSONL file (appended). Default: scripts/otel/otel-spans.jsonl",
    )
    parser.add_argument(
        "--source",
        default="dev",
        help="Default source tag (bob | dev). Overridden by X-Source header or service.name.",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    handler_cls = _make_handler(out_path, args.source)

    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(
        f"[otel] OTLP receiver listening on {args.host}:{args.port} -> {out_path}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[otel] shutting down.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
