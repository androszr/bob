"""Unit tests for scripts/teams_agent/stats.py -- perf aggregation and render."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))
import stats  # noqa: E402

_CET = stats._CET  # Europe/Warsaw, same as stats.py
_UTC = UTC


def _ts_today(hour: int = 10) -> str:
    """Return ISO UTC timestamp for today at given hour (CET midnight safe)."""
    now_cet = datetime.now(_CET).replace(hour=hour, minute=0, second=0, microsecond=0)
    return now_cet.astimezone(_UTC).isoformat()


def _ts_yesterday(hour: int = 10) -> str:
    """Return ISO UTC timestamp for yesterday at given hour."""
    now_cet = datetime.now(_CET).replace(hour=hour, minute=0, second=0, microsecond=0)
    yesterday_cet = now_cet - timedelta(days=1)
    return yesterday_cet.astimezone(_UTC).isoformat()


def _midnight_cet() -> datetime:
    """Return today's midnight CET (tz-aware), same logic as stats.py main()."""
    return datetime.now(_CET).replace(hour=0, minute=0, second=0, microsecond=0)


def _write_perf(path: Path, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_zero():
    assert stats._percentile([], 50) == 0.0
    assert stats._percentile([], 95) == 0.0


def test_percentile_single():
    assert stats._percentile([42.0], 50) == 42.0
    assert stats._percentile([42.0], 95) == 42.0


def test_percentile_p50_known():
    # [10, 20, 30, 40] sorted; nearest-rank p50: idx = int(50/100*4+0.5)-1 = int(2.5)-1 = 1 -> 20
    result = stats._percentile([10.0, 40.0, 20.0, 30.0], 50)
    assert result == 20.0


def test_percentile_p95_known():
    # [10,20,30,40]; p95: idx = int(0.95*4+0.5)-1 = int(4.3)-1 = 3 -> 40
    result = stats._percentile([10.0, 40.0, 20.0, 30.0], 95)
    assert result == 40.0


def test_percentile_two_elements_p50():
    assert stats._percentile([100.0, 200.0], 50) == 100.0


# ---------------------------------------------------------------------------
# _fmt_cost
# ---------------------------------------------------------------------------


def test_fmt_cost_small():
    assert stats._fmt_cost(0.0) == "$0.0000"
    assert stats._fmt_cost(0.001) == "$0.0010"
    assert stats._fmt_cost(0.009) == "$0.0090"


def test_fmt_cost_large():
    assert stats._fmt_cost(0.01) == "$0.01"
    assert stats._fmt_cost(1.5) == "$1.50"
    assert stats._fmt_cost(12.3456) == "$12.35"


# ---------------------------------------------------------------------------
# aggregate_perf -- missing file
# ---------------------------------------------------------------------------


def test_aggregate_perf_missing_file(tmp_path):
    """Missing file returns {"available": False} without raising."""
    result = stats.aggregate_perf(tmp_path / "nonexistent.jsonl", _midnight_cet())
    assert result == {"available": False}


# ---------------------------------------------------------------------------
# aggregate_perf -- bad JSON lines skipped
# ---------------------------------------------------------------------------


def test_aggregate_perf_bad_lines_skipped(tmp_path):
    """Corrupted lines are silently skipped; valid records still aggregated."""
    p = tmp_path / "perf.jsonl"
    good_record = {
        "ts": _ts_today(),
        "model": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_cost_usd": 0.002,
        "duration_ms": 1000,
        "ttft_ms": 300,
        "ctx": {"chat": "Alpha"},
    }
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{bad json\n")
        fh.write(json.dumps(good_record) + "\n")
        fh.write("also bad\n")

    result = stats.aggregate_perf(p, _midnight_cet())
    assert result["available"] is True
    assert result["count"] == 1
    assert result["total"]["input_tokens"] == 100


# ---------------------------------------------------------------------------
# aggregate_perf -- daily filter
# ---------------------------------------------------------------------------


def test_aggregate_perf_filters_yesterday(tmp_path):
    """Records with ts from yesterday are excluded."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_yesterday(),
            "model": "sonnet",
            "input_tokens": 999,
            "output_tokens": 999,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 9.99,
            "duration_ms": 5000,
            "ttft_ms": 1000,
            "ctx": {"chat": "Alpha"},
        },
        {
            "ts": _ts_today(),
            "model": "sonnet",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.002,
            "duration_ms": 1000,
            "ttft_ms": 300,
            "ctx": {"chat": "Alpha"},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    assert result["count"] == 1
    assert result["total"]["input_tokens"] == 100
    assert result["total"]["total_cost_usd"] == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# aggregate_perf -- token sums + per-chat + per-model
# ---------------------------------------------------------------------------


def test_aggregate_perf_token_sums(tmp_path):
    """aggregate_perf correctly sums tokens across multiple records."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(),
            "model": "haiku",
            "input_tokens": 200,
            "output_tokens": 30,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 100,
            "total_cost_usd": 0.001,
            "duration_ms": 500,
            "ttft_ms": 150,
            "ctx": {"chat": "Alpha"},
        },
        {
            "ts": _ts_today(),
            "model": "haiku",
            "input_tokens": 300,
            "output_tokens": 60,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.002,
            "duration_ms": 800,
            "ttft_ms": 200,
            "ctx": {"chat": "Alpha"},
        },
        {
            "ts": _ts_today(),
            "model": "sonnet",
            "input_tokens": 400,
            "output_tokens": 80,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.005,
            "duration_ms": 1200,
            "ttft_ms": 400,
            "ctx": {"chat": "Beta"},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    assert result["available"] is True
    assert result["count"] == 3

    t = result["total"]
    assert t["input_tokens"] == 900
    assert t["output_tokens"] == 170
    assert t["cache_read_input_tokens"] == 900
    assert t["cache_creation_input_tokens"] == 100
    assert t["total_cost_usd"] == pytest.approx(0.008)


def test_aggregate_perf_per_chat(tmp_path):
    """per_chat dict aggregates separately per chat name."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 100, "output_tokens": 10,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.001, "duration_ms": 300, "ttft_ms": 100,
            "ctx": {"chat": "Alpha"},
        },
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 200, "output_tokens": 20,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.002, "duration_ms": 400, "ttft_ms": 120,
            "ctx": {"chat": "Beta"},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    per_chat = result["per_chat"]
    assert per_chat["Alpha"]["input_tokens"] == 100
    assert per_chat["Beta"]["input_tokens"] == 200
    assert per_chat["Alpha"]["count"] == 1
    assert per_chat["Beta"]["count"] == 1


def test_aggregate_perf_per_model(tmp_path):
    """per_model dict aggregates separately per model name."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 100, "output_tokens": 10,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.001, "duration_ms": 300, "ttft_ms": 100,
            "ctx": {"chat": "Alpha"},
        },
        {
            "ts": _ts_today(), "model": "sonnet",
            "input_tokens": 500, "output_tokens": 80,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.005, "duration_ms": 1000, "ttft_ms": 350,
            "ctx": {"chat": "Alpha"},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    per_model = result["per_model"]
    assert per_model["haiku"]["count"] == 1
    assert per_model["sonnet"]["count"] == 1
    assert per_model["haiku"]["input_tokens"] == 100
    assert per_model["sonnet"]["input_tokens"] == 500


# ---------------------------------------------------------------------------
# aggregate_perf -- cache hit-rate
# ---------------------------------------------------------------------------


def test_aggregate_perf_cache_hit_rate(tmp_path):
    """Cache hit-rate = cache_read / (input + cache_read + cache_creation)."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.001,
            "duration_ms": 300, "ttft_ms": 100,
            "ctx": {},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    # hit_rate = 900 / (100 + 900 + 0) = 0.9
    assert result["total"]["cache_hit_rate"] == pytest.approx(0.9)


def test_aggregate_perf_cache_hit_rate_no_cache(tmp_path):
    """Cache hit-rate = 0 when no cache tokens at all (no divide by zero)."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.001,
            "duration_ms": 300, "ttft_ms": 100,
            "ctx": {},
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    assert result["total"]["cache_hit_rate"] == pytest.approx(0.0)


def test_aggregate_perf_zero_records_available_true(tmp_path):
    """Empty file (no records today) -> available True, count 0."""
    p = tmp_path / "perf.jsonl"
    p.write_text("", encoding="utf-8")

    result = stats.aggregate_perf(p, _midnight_cet())
    assert result["available"] is True
    assert result["count"] == 0


def test_aggregate_perf_fallback_missing_ctx(tmp_path):
    """Records without ctx or with non-dict ctx go to per_chat['']."""
    p = tmp_path / "perf.jsonl"
    records = [
        {
            "ts": _ts_today(), "model": "haiku",
            "input_tokens": 50, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.0005, "duration_ms": 200, "ttft_ms": 80,
            # no "ctx" key
        },
    ]
    _write_perf(p, records)

    result = stats.aggregate_perf(p, _midnight_cet())
    assert "" in result["per_chat"]
    assert result["per_chat"][""]["input_tokens"] == 50


# ---------------------------------------------------------------------------
# render_html -- resilience with perf=None and available=False
# ---------------------------------------------------------------------------


def _minimal_stats() -> dict:
    """Minimal stats dict for render_html (empty, no active chats)."""
    return {}


def test_render_html_perf_none_does_not_raise():
    """render_html with perf=None must not raise."""
    html = stats.render_html(_minimal_stats(), [], perf=None)
    assert isinstance(html, str)
    assert "brak danych" in html


def test_render_html_perf_not_available_shows_no_data():
    """render_html with perf={'available': False} shows brak danych message."""
    html = stats.render_html(_minimal_stats(), [], perf={"available": False})
    assert "brak danych" in html
    assert "perf-metrics.jsonl" in html


def test_render_html_perf_not_available_does_not_contain_dollar():
    """When perf is not available, no cost is shown."""
    html = stats.render_html(_minimal_stats(), [], perf={"available": False})
    assert "$" not in html


def _perf_two_chats(tmp_path):
    p = tmp_path / "perf.jsonl"
    rec = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 900,
           "cache_creation_input_tokens": 0, "duration_ms": 3000, "ttft_ms": 900}
    _write_perf(p, [
        {**rec, "ts": _ts_today(), "model": "claude-sonnet-5-5", "total_cost_usd": 3.0, "ctx": {"chat": "Projekt"}},
        {**rec, "ts": _ts_today(), "model": "claude-haiku-4-5", "total_cost_usd": 0.5, "ctx": {"chat": "Notatki"}},
        {**rec, "ts": _ts_yesterday(hour=1), "model": "claude-sonnet-5-5", "total_cost_usd": 7.0, "ctx": {}},
    ])
    return p


def test_render_html_headline_channels_and_notes(tmp_path):
    p = _perf_two_chats(tmp_path)
    perf = stats.aggregate_perf(p, _midnight_cet())
    activity = {"Projekt": {"wiadomosci": 10, "odpowiedz": 2, "milczenie": 8, "juz_odpowiedziane": 0, "last_ts": None}}
    html = stats.render_html(_minimal_stats(), [], perf=perf, activity=activity, costs=stats.daily_costs(p))
    assert "<strong>10</strong> wiadomości do Boba" in html and "<strong>2</strong> odpowiedzi" in html
    assert "<strong>8</strong> bez odpowiedzi" in html and "3,50 $" in html
    assert "<strong>Projekt</strong> to 86% dzisiejszego kosztu, 8 z 10 wiadomości bez odpowiedzi" in html
    assert html.index(">Projekt<") < html.index(">Notatki<")                 # most expensive channel first
    assert "Modele:" in html and "sonnet-5-5" in html                          # >1 model -> one line
    assert "Koszt - ostatnie 7 dni" in html and "dziś 4 $" in html
    assert "3,0 s" in html and "Latencja" not in html                          # plain words, no ms tables


def test_cost_compared_with_yesterday_at_the_same_time(tmp_path):
    p = _perf_two_chats(tmp_path)
    perf = stats.aggregate_perf(p, _midnight_cet())
    costs = stats.daily_costs(p)
    assert costs["yday_so_far"] == 7.0
    html = stats.render_html(_minimal_stats(), [], perf=perf, costs=costs)
    assert "wobec wczoraj o tej porze: 7,00 $" in html


def test_channel_activity_counts_bobs_decisions(tmp_path):
    log = tmp_path / "log.jsonl"
    rows = [{"ts": _ts_today(), "chat_name": "A", "action": "watch_send", "send_ok": True},
            {"ts": _ts_today(), "chat_name": "A", "action": "watch_skip_silent_sentinel"},
            {"ts": _ts_today(), "chat_name": "A", "action": "watch_skip_answered"},
            {"ts": _ts_today(), "chat_name": "A", "action": "watch_skip_empty"},
            {"ts": _ts_today(), "chat_name": "['A', 'B']", "action": "watch_stop"},
            {"ts": _ts_yesterday(), "chat_name": "A", "action": "watch_send", "send_ok": True}]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    a = stats.channel_activity(log, _midnight_cet())
    assert list(a) == ["A"]
    assert (a["A"]["wiadomosci"], a["A"]["odpowiedz"], a["A"]["milczenie"], a["A"]["juz_odpowiedziane"]) == (3, 1, 1, 1)


def test_unchanged_message_is_not_edited_again(monkeypatch):
    state = {"message_id": "today-1", "conv_id": "48:notes", "posted_at": _ts_today()}
    fake, saved = _patch_post(monkeypatch, state)
    stats.post_or_edit("<p>stan na 10:00</p><p>body</p>")
    assert len(fake.edit_calls) == 1 and saved["body"] == "<p>body</p>"
    fake2, _ = _patch_post(monkeypatch, {**state, "body": "<p>body</p>"})
    stats.post_or_edit("<p>stan na 10:10</p><p>body</p>")
    assert not fake2.edit_calls and not fake2.send_calls


def test_today_follows_warsaw_time_across_the_winter_change():
    assert str(stats._CET) == "Europe/Warsaw"


# ---------------------------------------------------------------------------
# post_or_edit -- daily rollover: new message per CET day, edit within the day
# ---------------------------------------------------------------------------

class _FakeApiClient:
    def __init__(self, edit_ok: bool = True, new_id: str = "new-999"):
        self.edit_ok = edit_ok
        self.new_id = new_id
        self.edit_calls: list[tuple] = []
        self.send_calls: list[tuple] = []

    def edit_message(self, msg_id, html, conv_id):
        self.edit_calls.append((msg_id, html, conv_id))
        return self.edit_ok

    def send_html_message(self, html, conv_id):
        self.send_calls.append((html, conv_id))
        return self.new_id


def _patch_post(monkeypatch, state: dict, edit_ok: bool = True):
    """Wire a fake api_client + in-memory state for post_or_edit."""
    fake = _FakeApiClient(edit_ok=edit_ok)
    monkeypatch.setitem(sys.modules, "api_client", fake)
    saved: dict = {}
    monkeypatch.setattr(stats, "_load_state", lambda: dict(state))
    monkeypatch.setattr(stats, "_save_state", lambda s: saved.update(s))
    return fake, saved


def test_post_or_edit_same_day_edits(monkeypatch):
    state = {"message_id": "today-1", "conv_id": "48:notes", "posted_at": _ts_today()}
    fake, saved = _patch_post(monkeypatch, state)
    stats.post_or_edit("<p>x</p>")
    assert fake.edit_calls and fake.edit_calls[0][0] == "today-1"
    assert not fake.send_calls  # same day -> edit only, no new message


def test_post_or_edit_new_day_posts_new(monkeypatch):
    state = {"message_id": "yday-1", "conv_id": "48:notes", "posted_at": _ts_yesterday()}
    fake, saved = _patch_post(monkeypatch, state)
    stats.post_or_edit("<p>x</p>")
    assert fake.send_calls  # new CET day -> brand new message
    assert not fake.edit_calls  # must NOT edit yesterday's message
    assert saved.get("message_id") == "new-999"


def test_post_or_edit_no_state_posts_new(monkeypatch):
    fake, saved = _patch_post(monkeypatch, {})
    stats.post_or_edit("<p>x</p>")
    assert fake.send_calls and not fake.edit_calls
    assert saved.get("message_id") == "new-999"


def test_post_or_edit_same_day_edit_fails_falls_back_to_new(monkeypatch):
    state = {"message_id": "today-1", "conv_id": "48:notes", "posted_at": _ts_today()}
    fake, saved = _patch_post(monkeypatch, state, edit_ok=False)
    stats.post_or_edit("<p>x</p>")
    assert fake.edit_calls and fake.send_calls  # edit tried, then fell back to send
    assert saved.get("message_id") == "new-999"


# ---------------------------------------------------------------------------
# aggregate_otel -- missing file
# ---------------------------------------------------------------------------


def _write_otel(path: Path, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_aggregate_otel_missing_file(tmp_path):
    """Missing file returns {"available": False} without raising."""
    result = stats.aggregate_otel(tmp_path / "nonexistent.jsonl", _midnight_cet())
    assert result == {"available": False}


def test_aggregate_otel_empty_file(tmp_path):
    """Empty file -> available True, per_tool empty."""
    p = tmp_path / "otel.jsonl"
    p.write_text("", encoding="utf-8")
    result = stats.aggregate_otel(p, _midnight_cet())
    assert result["available"] is True
    assert result["per_tool"] == {}


# ---------------------------------------------------------------------------
# aggregate_otel -- bad JSON lines skipped
# ---------------------------------------------------------------------------


def test_aggregate_otel_bad_lines_skipped(tmp_path):
    """Corrupted lines are silently skipped; valid records still aggregated."""
    p = tmp_path / "otel.jsonl"
    good_record = {
        "ts": _ts_today(),
        "span_type": "tool_use",
        "session_id": "sess-1",
        "tool_name": "Read",
        "duration_ms": 100.0,
        "success": True,
        "source": "dev",
    }
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{bad json\n")
        fh.write(json.dumps(good_record) + "\n")
        fh.write("also bad\n")

    result = stats.aggregate_otel(p, _midnight_cet())
    assert result["available"] is True
    assert "Read" in result["per_tool"]
    assert result["per_tool"]["Read"]["count"] == 1


# ---------------------------------------------------------------------------
# aggregate_otel -- per-tool aggregation + error_rate
# ---------------------------------------------------------------------------


def test_aggregate_otel_per_tool_counts(tmp_path):
    """per_tool aggregates count and duration for each tool_name."""
    p = tmp_path / "otel.jsonl"
    records = [
        {
            "ts": _ts_today(),
            "span_type": "tool_use",
            "session_id": "s1",
            "tool_name": "Read",
            "duration_ms": 50.0,
            "success": True,
            "source": "dev",
        },
        {
            "ts": _ts_today(),
            "span_type": "tool_use",
            "session_id": "s1",
            "tool_name": "Read",
            "duration_ms": 150.0,
            "success": True,
            "source": "dev",
        },
        {
            "ts": _ts_today(),
            "span_type": "tool_use",
            "session_id": "s1",
            "tool_name": "Bash",
            "duration_ms": 300.0,
            "success": False,
            "source": "dev",
        },
    ]
    _write_otel(p, records)

    result = stats.aggregate_otel(p, _midnight_cet())
    assert result["available"] is True
    pt = result["per_tool"]
    assert pt["Read"]["count"] == 2
    assert pt["Bash"]["count"] == 1
    # Bash had one error out of one call -> 100% error_rate
    assert pt["Bash"]["error_rate"] == pytest.approx(1.0)
    # Read had no errors
    assert pt["Read"]["error_rate"] == pytest.approx(0.0)


def test_aggregate_otel_per_tool_p50(tmp_path):
    """per_tool p50 computed correctly."""
    p = tmp_path / "otel.jsonl"
    records = [
        {
            "ts": _ts_today(),
            "span_type": "tool_use",
            "session_id": "s1",
            "tool_name": "Edit",
            "duration_ms": float(v),
            "success": True,
            "source": "dev",
        }
        for v in [100, 200, 300, 400]
    ]
    _write_otel(p, records)

    result = stats.aggregate_otel(p, _midnight_cet())
    pt = result["per_tool"]
    # [100,200,300,400] nearest-rank p50: same as _percentile([100,200,300,400], 50) = 200
    assert pt["Edit"]["p50_duration_ms"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# aggregate_otel -- since filter
# ---------------------------------------------------------------------------


def test_aggregate_otel_filters_yesterday(tmp_path):
    """Records from yesterday are excluded by the since filter."""
    p = tmp_path / "otel.jsonl"
    records = [
        {
            "ts": _ts_yesterday(),
            "span_type": "tool_use",
            "session_id": "s-old",
            "tool_name": "OldTool",
            "duration_ms": 999.0,
            "success": True,
            "source": "dev",
        },
        {
            "ts": _ts_today(),
            "span_type": "tool_use",
            "session_id": "s-new",
            "tool_name": "NewTool",
            "duration_ms": 100.0,
            "success": True,
            "source": "dev",
        },
    ]
    _write_otel(p, records)

    result = stats.aggregate_otel(p, _midnight_cet())
    assert "OldTool" not in result["per_tool"]
    assert "NewTool" in result["per_tool"]


# ---------------------------------------------------------------------------
# render_html -- otel=None and otel available smoke tests
# ---------------------------------------------------------------------------


def test_render_html_otel_none_does_not_raise():
    """render_html with otel=None must not raise."""
    html = stats.render_html(_minimal_stats(), [], otel=None)
    assert isinstance(html, str)


def test_render_html_leaves_otel_tables_to_the_panel(tmp_path):
    """The phone message has no OTEL tables (no data source today); aggregate_otel stays usable for scripts."""
    p = tmp_path / "otel.jsonl"
    _write_otel(p, [{"ts": _ts_today(), "span_type": "tool_use", "session_id": "s1", "tool_name": "Read",
                     "duration_ms": 120.0, "success": True, "source": "dev"}])
    otel = stats.aggregate_otel(p, _midnight_cet())
    assert otel.get("available")
    for o in (otel, {"available": False}):
        html = stats.render_html(_minimal_stats(), [], otel=o)
        assert "OTEL" not in html and "brak danych OTEL" not in html


def test_daily_costs_sum_money_as_decimals(tmp_path):
    from decimal import Decimal  # noqa: PLC0415
    p = tmp_path / "perf.jsonl"
    _write_perf(p, [{"ts": _ts_today(), "total_cost_usd": 0.1} for _ in range(3)]
                + [{"ts": _ts_today(), "total_cost_usd": "n/a"}])
    costs = stats.daily_costs(p)
    today = list(costs["days"].values())[-1]
    # as floats 0.1 + 0.1 + 0.1 would be 0.30000000000000004
    assert today == Decimal("0.3") and isinstance(today, Decimal)
    assert "0,30 $" in stats._usd(today)


def test_daily_costs_without_a_file_has_no_yesterday_figure(tmp_path):
    costs = stats.daily_costs(tmp_path / "nie-ma.jsonl")
    assert costs["yday_so_far"] is None and len(costs["days"]) == 7
