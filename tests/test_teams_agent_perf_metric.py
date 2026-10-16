"""Unit tests for perf metric instrumentation in scripts/teams_agent/ai.py."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))
import ai  # noqa: E402

_MODEL = "claude-sonnet-4-6"

_FULL_JSON_STDOUT = json.dumps({
    "result": "hej",
    "duration_ms": 1234,
    "duration_api_ms": 1100,
    "ttft_ms": 300,
    "total_cost_usd": 0.001,
    "is_error": False,
    "usage": {
        "input_tokens": 500,
        "output_tokens": 50,
        "cache_creation_input_tokens": 9164,
        "cache_read_input_tokens": 17674,
    },
})


def _make_proc(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


def test_invoke_claude_parses_result_field(tmp_path):
    """_invoke_claude returns the 'result' field text, not raw JSON."""
    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == "hej"
    assert "{" not in result  # must not return raw JSON


def test_invoke_claude_fallback_non_json(tmp_path):
    """When stdout is plain text (non-JSON), _invoke_claude does not raise
    and returns the sanitized raw text."""
    with patch("subprocess.run", return_value=_make_proc("plain text response")):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == "plain text response"


def test_invoke_claude_fallback_json_without_result_field(tmp_path):
    """JSON output without 'result' key triggers fallback (KeyError) and
    returns sanitized raw stdout."""
    bad_json = json.dumps({"foo": "bar", "duration_ms": 100})
    with patch("subprocess.run", return_value=_make_proc(bad_json)):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == bad_json


def test_invoke_claude_is_error_true_returns_raw(tmp_path):
    """When is_error=True, _invoke_claude returns sanitized raw stdout as fallback."""
    err_json = json.dumps({
        "result": "",
        "is_error": True,
        "duration_ms": 100,
        "usage": {},
    })
    with patch("subprocess.run", return_value=_make_proc(err_json)):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    # Must return sanitized raw stdout, not raise
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Metrics recording
# ---------------------------------------------------------------------------


def test_record_perf_metric_writes_jsonl(tmp_path):
    """_record_perf_metric appends exactly one JSON line to _PERF_LOG."""
    log_path = tmp_path / "perf-metrics.jsonl"
    record = {"model": _MODEL, "duration_ms": 1234, "total_cost_usd": 0.001}

    with patch.object(ai, "_PERF_LOG", log_path):
        ai._record_perf_metric(record)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["model"] == _MODEL
    assert parsed["duration_ms"] == 1234
    assert parsed["total_cost_usd"] == 0.001


def test_record_perf_metric_appends(tmp_path):
    """Multiple calls each append one line (no overwrite)."""
    log_path = tmp_path / "perf-metrics.jsonl"

    with patch.object(ai, "_PERF_LOG", log_path):
        ai._record_perf_metric({"n": 1})
        ai._record_perf_metric({"n": 2})

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["n"] == 1
    assert json.loads(lines[1])["n"] == 2


def test_invoke_claude_writes_metric_on_success(tmp_path):
    """After a successful call with valid JSON output, one metric line is written
    containing expected keys."""
    log_path = tmp_path / "perf-metrics.jsonl"

    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_PERF_LOG", log_path):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == "hej"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    metric = json.loads(lines[0])
    assert metric["total_cost_usd"] == 0.001
    assert metric["duration_ms"] == 1234
    assert metric["input_tokens"] == 500
    assert metric["output_tokens"] == 50
    assert metric["cache_read_input_tokens"] == 17674
    assert metric["model"] == _MODEL
    assert "ts" in metric


def test_invoke_claude_metric_contains_ctx(tmp_path):
    """metrics_ctx is stored in metric['ctx']."""
    log_path = tmp_path / "perf-metrics.jsonl"
    ctx = {"chat": "Kanal AI"}

    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_PERF_LOG", log_path):
            ai._invoke_claude("hello", _MODEL, 30, metrics_ctx=ctx)

    metric = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert metric["ctx"] == ctx


def test_invoke_claude_metric_ctx_defaults_to_empty(tmp_path):
    """When metrics_ctx is None (default), ctx in metric is {}."""
    log_path = tmp_path / "perf-metrics.jsonl"

    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_PERF_LOG", log_path):
            ai._invoke_claude("hello", _MODEL, 30)

    metric = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert metric["ctx"] == {}


# ---------------------------------------------------------------------------
# Resilience: metric write failure must not break the reply
# ---------------------------------------------------------------------------


def test_invoke_claude_metric_write_error_does_not_raise(tmp_path):
    """If _record_perf_metric raises, _invoke_claude still returns the reply."""
    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_record_perf_metric", side_effect=OSError("disk full")):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == "hej"


def test_record_perf_metric_write_error_does_not_raise(tmp_path):
    """_record_perf_metric itself must not raise on write error."""
    # Point _PERF_LOG at an unwritable path (a directory).
    log_dir = tmp_path / "perf-metrics.jsonl"
    log_dir.mkdir()  # create a directory at the path — write will fail

    with patch.object(ai, "_PERF_LOG", log_dir):
        # Must not raise
        ai._record_perf_metric({"model": _MODEL})


# ---------------------------------------------------------------------------
# Backward-compatibility: non-zero returncode, timeout, FileNotFoundError
# ---------------------------------------------------------------------------


def test_invoke_claude_nonzero_returncode_returns_empty(tmp_path):
    """Non-zero returncode still returns empty string (unchanged behaviour)."""
    with patch("subprocess.run", return_value=_make_proc("error output", returncode=1)):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == ""
    # No metric written for returncode != 0
    assert not (tmp_path / "perf-metrics.jsonl").exists()


def test_invoke_claude_timeout_returns_timeout_reply(tmp_path):
    """TimeoutExpired returns timeout_reply (unchanged behaviour)."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30),
    ):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30, timeout_reply="TIMEOUT")

    assert result == "TIMEOUT"


def test_invoke_claude_file_not_found_returns_empty(tmp_path):
    """FileNotFoundError returns empty string (unchanged behaviour)."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with patch.object(ai, "_PERF_LOG", tmp_path / "perf-metrics.jsonl"):
            result = ai._invoke_claude("hello", _MODEL, 30)

    assert result == ""


# ---------------------------------------------------------------------------
# freeform_reply forwards metrics_ctx
# ---------------------------------------------------------------------------


def test_freeform_reply_forwards_metrics_ctx(tmp_path):
    """freeform_reply passes metrics_ctx to _invoke_claude (verified via metric)."""
    log_path = tmp_path / "perf-metrics.jsonl"
    ctx = {"chat": "Test Chat"}

    with patch("subprocess.run", return_value=_make_proc(_FULL_JSON_STDOUT)):
        with patch.object(ai, "_PERF_LOG", log_path):
            result = ai.freeform_reply("pytanie", metrics_ctx=ctx)

    assert result == "hej"
    metric = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert metric["ctx"] == ctx
